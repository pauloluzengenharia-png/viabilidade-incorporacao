"""
Sistema de viabilidade de incorporação — API e telas.

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import datetime as dt
import io
import pathlib
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import aplicar_migrations, q, sessao
from .importadores import gravar
from .importadores.sienge import (ler_planilha, normalizar_contratos,
                                  normalizar_movimentos, normalizar_recebido,
                                  normalizar_receber, normalizar_unidades)
from . import edicao, planilha, procedencia
from .glossario import SECOES, ajuda, ajuda_da_linha
from .novo_estudo import FORMATOS_CURVA, Estudo
from .novo_estudo import curva as curva_de
from .novo_estudo import somar_meses
from .novo_estudo import criar as criar_estudo
from .repositorio import carregar_entradas
from .seguranca import (COOKIE, DURACAO, conferir_senha, credenciais_configuradas,
                        criar_sessao, exigir_login, usuario_atual)
from .seguranca import registrar as registrar_seguranca
from .servico import calcular, rodar_e_persistir, visao_viabilidade
from . import cronograma
from .pdp import PDP

AQUI = pathlib.Path(__file__).resolve().parent
# a dependência vale para TODAS as rotas; `exigir_login` libera só o health check
app = FastAPI(title="Viabilidade de Incorporação", docs_url="/api/docs",
              dependencies=[Depends(exigir_login)])
registrar_seguranca(app)
app.mount("/static", StaticFiles(directory=AQUI / "web" / "static"), name="static")
templates = Jinja2Templates(directory=str(AQUI / "web" / "templates"))


# ---------------------------------------------------------------- filtros
def moeda(v, casas=0):
    if v is None:
        return "—"
    return f"{float(v):,.{casas}f}".replace(",", " ").replace(".", ",")


def milhoes(v):
    if v is None:
        return "—"
    return f"{float(v)/1_000_000:,.1f}".replace(".", ",")


def percentual(v, casas=1):
    if v is None:
        return "—"
    return f"{float(v)*100:,.{casas}f}%".replace(".", ",")


def mes_curto(d):
    if not d:
        return "—"
    return f"{d.month:02d}/{d.year}"


def dinheiro(v):
    """R$ com no mínimo duas casas e as que o valor tiver além disso."""
    return edicao.moeda(v)


def enfase(texto):
    """
    O grifo do glossário, e nada além dele.

    Os verbetes são escritos em texto puro e marcam o termo importante com
    `**assim**`, ou `*assim*` quando é só um contraste — é o que se digita
    naturalmente e o que sobrevive a virar planilha ou e-mail. Aqui isso vira
    negrito e itálico na tela. Tudo o mais é escapado antes: o texto continua
    sendo dado, não HTML.
    """
    import re
    from markupsafe import Markup, escape
    if not texto:
        return ""
    marcado = str(escape(texto))
    marcado = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", marcado)
    marcado = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", marcado)
    return Markup(marcado)


templates.env.filters.update(moeda=moeda, milhoes=milhoes, dinheiro=dinheiro,
                             percentual=percentual, mes=mes_curto, enfase=enfase)
# a explicação de cada linha mora no glossário; o template só a pendura
def _slug(rotulo: str) -> str:
    """Um identificador de URL estável a partir do rótulo da conta."""
    import hashlib
    return hashlib.sha1(rotulo.encode()).hexdigest()[:10]


def rotulo_do_campo(chave: str) -> str:
    """Nome legível de um campo, para o histórico não falar em chave de banco."""
    for mod in edicao.MODULOS.values():
        for c in mod.campos:
            if c.chave == chave:
                return c.rotulo
    v = ajuda(chave)
    return v.titulo if v else chave


templates.env.globals.update(ajuda_da_linha=ajuda_da_linha, ajuda=ajuda,
                             rotulo_do_campo=rotulo_do_campo,
                             slug_da_linha=_slug)


@app.on_event("startup")
def preparar():
    aplicar_migrations()


# ================================================================ login
@app.get("/entrar", response_class=HTMLResponse)
def tela_entrar(request: Request, de: str = "/"):
    if credenciais_configuradas() is None:
        raise HTTPException(503, "sem senha configurada")
    return templates.TemplateResponse(request, "entrar.html", {"de": de, "erro": None})


@app.post("/entrar")
def entrar(request: Request, usuario: str = Form(...), senha: str = Form(...),
           de: str = Form("/")):
    if not conferir_senha(usuario, senha):
        # a mensagem nao diz se o errado foi o usuario ou a senha
        return templates.TemplateResponse(
            request, "entrar.html",
            {"de": de, "usuario": usuario, "erro": "Usuário ou senha não conferem."},
            status_code=401)

    destino = de if de.startswith("/") and not de.startswith("//") else "/"
    resposta = RedirectResponse(destino, status_code=303)
    resposta.set_cookie(
        COOKIE, criar_sessao(usuario), max_age=DURACAO,
        httponly=True,                      # fora do alcance de JavaScript
        samesite="lax",                     # nao viaja em requisicao de outro site
        secure=request.url.scheme == "https")
    return resposta


@app.get("/sair")
def sair():
    resposta = RedirectResponse("/entrar", status_code=303)
    resposta.delete_cookie(COOKIE)
    return resposta


# ================================================================ abertura da linha
def _linha_por_slug(slug: str) -> Optional[str]:
    from .servico import LINHAS_DRE
    return next((r for r, _ in LINHAS_DRE if _slug(r) == slug), None)


@app.get("/empreendimento/{emp_id}/linha/{slug}", response_class=HTMLResponse)
def tela_linha(emp_id: int, slug: str, request: Request,
               ate: Optional[str] = None, cenario: Optional[int] = None,
               s: Session = Depends(sessao)):
    """
    A linha do resultado aberta por dentro: como se calcula, com que insumos,
    quais lançamentos somam o realizado e como o valor se distribui no tempo.
    """
    rotulo = _linha_por_slug(slug)
    if not rotulo:
        raise HTTPException(404, "linha não encontrada")

    emp = _empreendimento(s, emp_id)
    corte = _data(ate) or emp.get("mes_corte_realizado")
    cen = _cenario_escolhido(s, emp_id, cenario)

    entradas = carregar_entradas(s, cen["id"])
    dre, fluxo, _ = calcular(entradas)
    from .motor.engine import calcular_vgv
    bloco = calcular_vgv(entradas.unidades, entradas.premissas, entradas.tabela)

    setor, itens = procedencia.composicao_da_linha(s, cen["id"], rotulo)
    formula = procedencia.formula_da_linha(rotulo, dre, bloco, entradas.obra,
                                           entradas.premissas, itens)
    chaves = [t.chave for t in (formula.termos if formula else []) if t.chave]
    ctx = {
        "setor": setor,
        "emp": emp, "aba": "viabilidade", "rotulo": rotulo, "corte": corte,
        "cenario": cen, "cenarios": _cenarios(s, emp_id),
        "formula": formula,
        "insumos": procedencia.origem_das_premissas(s, cen["id"], chaves),
        "realizado": procedencia.lancamentos_da_linha(s, emp_id, rotulo, corte),
        "projecao": procedencia.projecao_da_linha(s, emp_id, cen["id"], rotulo),
        "importacoes": procedencia.importacoes(s, emp_id),
        "verbete": ajuda_da_linha(rotulo),
        "slug": slug,
        "linha_tabela": next(
            (l for l in visao_viabilidade(s, emp_id, corte) if l["linha"] == rotulo),
            None),
    }
    return templates.TemplateResponse(request, "linha.html", ctx)


@app.get("/empreendimento/{emp_id}/linha/{slug}/planilha")
def baixar_memoria(emp_id: int, slug: str, ate: Optional[str] = None,
                   cenario: Optional[int] = None, s: Session = Depends(sessao)):
    """A mesma abertura da linha, em planilha, com os resultados como fórmula."""
    from fastapi.responses import Response

    rotulo = _linha_por_slug(slug)
    if not rotulo:
        raise HTTPException(404, "linha não encontrada")
    emp = _empreendimento(s, emp_id)
    corte = _data(ate) or emp.get("mes_corte_realizado")
    cen = _cenario_escolhido(s, emp_id, cenario)

    entradas = carregar_entradas(s, cen["id"])
    dre, _, _ = calcular(entradas)
    from .motor.engine import calcular_vgv
    bloco = calcular_vgv(entradas.unidades, entradas.premissas, entradas.tabela)
    _, itens = procedencia.composicao_da_linha(s, cen["id"], rotulo)
    formula = procedencia.formula_da_linha(rotulo, dre, bloco, entradas.obra,
                                           entradas.premissas, itens)
    chaves = [t.chave for t in (formula.termos if formula else []) if t.chave]

    conteudo = planilha.memoria_de_calculo(
        emp=emp, cenario=cen, rotulo=rotulo, corte=corte, formula=formula,
        insumos=procedencia.origem_das_premissas(s, cen["id"], chaves),
        realizado=procedencia.lancamentos_da_linha(s, emp_id, rotulo, corte),
        projecao=procedencia.projecao_da_linha(s, emp_id, cen["id"], rotulo),
        linha_tabela=next(
            (l for l in visao_viabilidade(s, emp_id, corte) if l["linha"] == rotulo),
            None),
        verbete=ajuda_da_linha(rotulo))

    limpo = "".join(ch if ch.isalnum() or ch in " -_" else ""
                    for ch in rotulo).strip().replace(" ", "-")
    nome = f"memoria-{limpo}-{cen['nome']}.xlsx"
    return Response(
        conteudo,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ================================================================ dados
#: os módulos na ordem em que aparecem na sub-navegação
MENU_DADOS = [
    {"slug": "cadastro",  "titulo": "Cadastro"},
    {"slug": "premissas", "titulo": "Premissas"},
    {"slug": "comercial", "titulo": "Preço e tabela"},
    {"slug": "plano",     "titulo": "Plano de vendas"},
    {"slug": "custos",    "titulo": "Custos"},
    {"slug": "obra",      "titulo": "Obra"},
    {"slug": "terreno",   "titulo": "Terreno"},
    {"slug": "unidades",  "titulo": "Unidades"},
    {"slug": "cronograma", "titulo": "Cronograma"},
]


def _cenarios(s: Session, emp_id: int) -> list[dict]:
    return q(s, """SELECT id, nome, tipo, principal FROM cenario
                    WHERE empreendimento_id = :e
                    ORDER BY tipo DESC, principal DESC, nome""", e=emp_id)


def _cenario_escolhido(s: Session, emp_id: int, pedido: Optional[int]) -> dict:
    cs = _cenarios(s, emp_id)
    if not cs:
        raise HTTPException(404, "empreendimento sem cenário cadastrado")
    if pedido:
        achado = next((c for c in cs if c["id"] == pedido), None)
        if achado:
            return achado
    return next((c for c in cs if c["principal"]), cs[0])


def _contexto_dados(s: Session, emp_id: int, modulo: str) -> dict:
    return {"emp": _empreendimento(s, emp_id), "menu_dados": MENU_DADOS,
            "modulo_atual": modulo, "aba": "dados"}


@app.get("/empreendimento/{emp_id}/dados", response_class=HTMLResponse)
def tela_dados(emp_id: int, request: Request, s: Session = Depends(sessao)):
    """O hub: cada módulo com o resumo do que já tem dentro."""
    ctx = _contexto_dados(s, emp_id, "")
    cen = _cenario_escolhido(s, emp_id, None)
    n = lambda sql, **kw: q(s, sql, **kw)[0]["n"]  # noqa: E731
    ctx["resumo"] = {
        "cadastro": f"{ctx['emp']['area_privativa']:,.0f} m² privativos".replace(",", " "),
        "premissas": f"{n('SELECT count(*) AS n FROM premissa WHERE cenario_id = :c', c=cen['id'])} premissas · cenário {cen['nome']}",
        "comercial": f"{n('SELECT count(*) AS n FROM tabela_venda WHERE empreendimento_id = :e', e=emp_id)} tabela(s) de venda",
        "plano": f"{n('SELECT coalesce(sum(quantidade),0) AS n FROM plano_venda WHERE cenario_id = :c', c=cen['id'])} unidades no plano",
        "custos": f"{n('SELECT count(*) AS n FROM composicao_item WHERE cenario_id = :c', c=cen['id'])} item(ns) de composição",
        "obra": f"{n('SELECT count(*) AS n FROM orcamento_obra WHERE empreendimento_id = :e', e=emp_id)} versão(ões) de orçamento",
        "terreno": f"{n('SELECT count(*) AS n FROM premissa_terreno WHERE cenario_id = :c', c=cen['id'])} parcela(s)",
        "unidades": f"{n('SELECT count(*) AS n FROM unidade WHERE empreendimento_id = :e', e=emp_id)} unidades",
    }
    nm = n('SELECT count(*) AS n FROM marco WHERE empreendimento_id = :e', e=emp_id)
    ctx["resumo"]["cronograma"] = (f"{nm} marcos do PDP" if nm
                                   else "sem cronograma sincronizado")
    ctx["alteracoes"] = edicao.historico(s, emp_id, limite=6)
    return templates.TemplateResponse(request, "dados.html", ctx)


@app.get("/empreendimento/{emp_id}/dados/historico", response_class=HTMLResponse)
def tela_historico(emp_id: int, request: Request, s: Session = Depends(sessao)):
    ctx = _contexto_dados(s, emp_id, "historico")
    ctx["eventos"] = edicao.historico(s, emp_id, limite=300)
    return templates.TemplateResponse(request, "historico.html", ctx)


# ---------------------------------------------------------- cronograma
@app.get("/empreendimento/{emp_id}/dados/cronograma", response_class=HTMLResponse)
def tela_cronograma(emp_id: int, request: Request, s: Session = Depends(sessao)):
    """O cronograma do PDP visto pelo estudo: cada área e o setor que ela paga."""
    ctx = _contexto_dados(s, emp_id, "cronograma")
    ctx.update(areas=cronograma.por_area(s, emp_id),
               ultima=cronograma.ultima_sincronizacao(s, emp_id),
               configurado=PDP.configurado())
    return templates.TemplateResponse(request, "cronograma.html", ctx)


@app.post("/empreendimento/{emp_id}/dados/cronograma/sincronizar")
def sincronizar_cronograma(emp_id: int, s: Session = Depends(sessao),
                           autor: str = Depends(usuario_atual)):
    """Lê o PDP e substitui o cronograma. Nunca estoura: a tela conta o que houve."""
    cronograma.sincronizar(s, emp_id, autor)
    s.commit()
    return RedirectResponse(f"/empreendimento/{emp_id}/dados/cronograma",
                            status_code=303)


@app.post("/empreendimento/{emp_id}/dados/cronograma/importar")
async def importar_cronograma(emp_id: int, arquivo: UploadFile = File(...),
                              s: Session = Depends(sessao),
                              autor: str = Depends(usuario_atual)):
    """O caminho que funciona quando o PDP não aceita acesso de servidor."""
    cronograma.importar(s, emp_id, await arquivo.read(), autor)
    s.commit()
    return RedirectResponse(f"/empreendimento/{emp_id}/dados/cronograma",
                            status_code=303)


# ------------------------------------------------------ setores de custo
@app.get("/empreendimento/{emp_id}/dados/custos", response_class=HTMLResponse)
def tela_custos(emp_id: int, request: Request, cenario: Optional[int] = None,
                s: Session = Depends(sessao)):
    """O hub dos setores: cada um com quantos itens tem e quanto soma."""
    ctx = _contexto_dados(s, emp_id, "custos")
    cen = _cenario_escolhido(s, emp_id, cenario)
    ctx.update(cenarios=_cenarios(s, emp_id), cenario=cen,
               congelado=bool(cen["tipo"] == "orcado"), salvo=None, erros=[],
               setores=edicao.setores(s), totais=edicao.totais_por_setor(s, cen["id"]))
    return templates.TemplateResponse(request, "custos.html", ctx)


@app.get("/empreendimento/{emp_id}/dados/custos/{codigo}", response_class=HTMLResponse)
def tela_setor(emp_id: int, codigo: str, request: Request,
               cenario: Optional[int] = None, salvo: Optional[str] = None,
               s: Session = Depends(sessao)):
    st = edicao.setor(s, codigo)
    if not st:
        raise HTTPException(404, "setor de custo não encontrado")
    ctx = _contexto_dados(s, emp_id, "custos")
    cen = _cenario_escolhido(s, emp_id, cenario)
    itens = edicao.ler_composicao(s, cen["id"], codigo)
    ctx.update(cenarios=_cenarios(s, emp_id), cenario=cen,
               congelado=bool(cen["tipo"] == "orcado"),
               salvo=salvo.split("|") if salvo else None, erros=[],
               setor=st, itens=itens,
               marcos=cronograma.marcos_para_escolha(s, emp_id, codigo),
               janela=cronograma.resumo_do_setor(s, emp_id, cen["id"], codigo),
               total=sum(float(i["valor"]) for i in itens))
    return templates.TemplateResponse(request, "custo_setor.html", ctx)


@app.post("/empreendimento/{emp_id}/dados/custos/{codigo}")
async def salvar_setor(emp_id: int, codigo: str, request: Request,
                       cenario: Optional[int] = None,
                       s: Session = Depends(sessao),
                       autor: str = Depends(usuario_atual)):
    st = edicao.setor(s, codigo)
    if not st:
        raise HTTPException(404, "setor de custo não encontrado")
    cen = _cenario_escolhido(s, emp_id, cenario)
    if cen["tipo"] == "orcado":
        raise HTTPException(409, "cenário congelado não aceita edição")

    f = await request.form()
    itens = []
    marcos_enviados = f.getlist("marco_id") or [""] * len(f.getlist("descricao"))
    for descricao, qtd, unidade, unitario, valor, obs, marco in zip(
            f.getlist("descricao"), f.getlist("quantidade"), f.getlist("unidade"),
            f.getlist("valor_unitario"), f.getlist("valor"), f.getlist("observacao"),
            marcos_enviados):
        quantidade = edicao.num(qtd)
        unit = edicao.num(unitario)
        # quantidade × unitário manda quando os dois existem; o total digitado
        # vale quando o item é verba fechada
        total = quantidade * unit if quantidade and unit else edicao.num(valor)
        itens.append({"descricao": descricao, "quantidade": quantidade or None,
                      "unidade": unidade, "valor_unitario": unit or None,
                      "valor": total, "observacao": obs,
                      "marco_id": int(marco) if marco else None})

    try:
        mudou = edicao.gravar_composicao(s, emp_id=emp_id, cenario_id=cen["id"],
                                         codigo=codigo, itens=itens, autor=autor)
        s.commit()
    except Exception as e:                          # noqa: BLE001
        s.rollback()
        raise HTTPException(422, f"o banco recusou: {e}")

    destino = (f"/empreendimento/{emp_id}/dados/custos/{codigo}"
               f"?salvo={'|'.join(mudou)}&cenario={cen['id']}")
    return RedirectResponse(destino, status_code=303)


# ------------------------------------------------ módulos de campos simples
@app.get("/empreendimento/{emp_id}/dados/{slug}", response_class=HTMLResponse)
def tela_modulo(emp_id: int, slug: str, request: Request,
                cenario: Optional[int] = None, salvo: Optional[str] = None,
                s: Session = Depends(sessao)):
    if slug in ("plano", "obra", "terreno", "unidades"):
        return _tela_lista(emp_id, slug, request, cenario, salvo, s)
    mod = edicao.MODULOS.get(slug)
    if not mod:
        raise HTTPException(404, "módulo não encontrado")
    ctx = _contexto_dados(s, emp_id, slug)
    cen = _cenario_escolhido(s, emp_id, cenario) if mod.por_cenario else None
    ctx.update(mod=mod, cenarios=_cenarios(s, emp_id) if mod.por_cenario else [],
               cenario=cen, congelado=bool(cen and cen["tipo"] == "orcado"),
               valores=edicao.LEITORES[slug](s, emp_id, cen["id"] if cen else None),
               salvo=salvo.split("|") if salvo else None, erros=[])
    return templates.TemplateResponse(request, "editar.html", ctx)


@app.post("/empreendimento/{emp_id}/dados/{slug}")
async def salvar_modulo(emp_id: int, slug: str, request: Request,
                        cenario: Optional[int] = None,
                        s: Session = Depends(sessao),
                        autor: str = Depends(usuario_atual)):
    mod = edicao.MODULOS.get(slug)
    if not mod:
        raise HTTPException(404, "módulo não encontrado")
    cen = _cenario_escolhido(s, emp_id, cenario) if mod.por_cenario else None
    if cen and cen["tipo"] == "orcado":
        raise HTTPException(409, "cenário congelado não aceita edição")

    enviado = dict(await request.form())
    erros = _conferir_soma(mod, enviado)
    if erros:
        ctx = _contexto_dados(s, emp_id, slug)
        ctx.update(mod=mod, cenarios=_cenarios(s, emp_id) if mod.por_cenario else [],
                   cenario=cen, congelado=False, valores=enviado,
                   salvo=None, erros=erros)
        return templates.TemplateResponse(request, "editar.html", ctx,
                                          status_code=422)

    try:
        mudou = edicao.GRAVADORES[slug](s, emp_id, cen["id"] if cen else None,
                                        enviado, autor)
        s.commit()
    except Exception as e:                          # noqa: BLE001 — a tela mostra
        s.rollback()
        ctx = _contexto_dados(s, emp_id, slug)
        ctx.update(mod=mod, cenarios=_cenarios(s, emp_id) if mod.por_cenario else [],
                   cenario=cen, congelado=False, valores=enviado, salvo=None,
                   erros=[f"O banco recusou: {e}"])
        return templates.TemplateResponse(request, "editar.html", ctx,
                                          status_code=422)

    # o aviso fala o nome do campo, não a chave: "Marketing — stand", não
    # "marketing_stand"
    rotulos = {c.chave: c.rotulo for c in mod.campos}
    return _voltar(emp_id, slug, [rotulos.get(k, k) for k in mudou], cen)


def _conferir_soma(mod, enviado: dict) -> list[str]:
    """A trava é o CHECK do banco; isto existe para a mensagem ser decente."""
    if not mod.soma_cem:
        return []
    total = sum(edicao.num(enviado.get(k)) for k in mod.soma_cem)
    if abs(total - 1) > 1e-6:
        return [f"A tabela de venda soma {total*100:.2f}% e precisa somar "
                f"exatamente 100%. Uma tabela que não fecha inventa um desconto — "
                f"ou uma receita — que ninguém aprovou."]
    return []


# ------------------------------------------------------- módulos de lista
def _tela_lista(emp_id: int, slug: str, request: Request,
                cenario: Optional[int], salvo: Optional[str],
                s: Session) -> HTMLResponse:
    ctx = _contexto_dados(s, emp_id, slug)
    por_cenario = slug in ("plano", "terreno")
    cen = _cenario_escolhido(s, emp_id, cenario) if por_cenario else None
    ctx.update(cenarios=_cenarios(s, emp_id) if por_cenario else [],
               cenario=cen, congelado=bool(cen and cen["tipo"] == "orcado"),
               salvo=salvo.split("|") if salvo else None, erros=[])

    if slug == "plano":
        ctx["linhas"] = edicao.ler_plano(s, cen["id"])
        ctx["total"] = sum(int(l["quantidade"]) for l in ctx["linhas"])
        ctx["estoque"] = q(s, """SELECT count(*) AS n FROM unidade
                                  WHERE empreendimento_id = :e
                                    AND considerar_na_viabilidade
                                    AND situacao = 'Disponível'""", e=emp_id)[0]["n"]
        return templates.TemplateResponse(request, "editar_plano.html", ctx)

    if slug == "terreno":
        ctx["parcelas"] = edicao.ler_terreno(s, cen["id"])
        ctx["total"] = sum(float(p["valor"]) for p in ctx["parcelas"])
        return templates.TemplateResponse(request, "editar_terreno.html", ctx)

    if slug == "obra":
        ctx.update(edicao.ler_obra(s, emp_id))
        ctx["soma_curva"] = sum(float(c["perc_fisico"]) for c in ctx["curva"])
        ctx["formatos"] = FORMATOS_CURVA
        return templates.TemplateResponse(request, "editar_obra.html", ctx)

    ctx["unidades"] = edicao.ler_unidades(s, emp_id)
    ctx["situacoes"] = ("Disponível", "Reservada", "Vendida", "Permuta", "Distratada")
    ctx["tipos"] = ("Normal", "Investidor", "Leal", "Garagem")
    ctx["no_vgv"] = sum(1 for u in ctx["unidades"] if u["considerar_na_viabilidade"])
    ctx["area_vgv"] = sum(float(u["area_privativa"]) for u in ctx["unidades"]
                          if u["considerar_na_viabilidade"])
    return templates.TemplateResponse(request, "editar_unidades.html", ctx)


def _voltar(emp_id: int, slug: str, mudou: list, cen: Optional[dict]) -> RedirectResponse:
    destino = f"/empreendimento/{emp_id}/dados/{slug}?salvo=" + "|".join(mudou)
    if cen:
        destino += f"&cenario={cen['id']}"
    return RedirectResponse(destino, status_code=303)


@app.post("/empreendimento/{emp_id}/dados/plano/salvar")
async def salvar_plano(emp_id: int, request: Request, cenario: Optional[int] = None,
                       s: Session = Depends(sessao),
                       autor: str = Depends(usuario_atual)):
    cen = _cenario_escolhido(s, emp_id, cenario)
    if cen["tipo"] == "orcado":
        raise HTTPException(409, "cenário congelado não aceita edição")
    f = await request.form()
    linhas = []
    for mes, tipo, qtd in zip(f.getlist("mes"), f.getlist("tipo"),
                              f.getlist("quantidade")):
        d = edicao.data(mes)
        if d:
            linhas.append((edicao.fim_do_mes(d), tipo or "Normal",
                           edicao.inteiro(qtd)))
    mudou = edicao.gravar_plano(s, emp_id=emp_id, cenario_id=cen["id"],
                                linhas=linhas, autor=autor)
    s.commit()
    return _voltar(emp_id, "plano", mudou, cen)


@app.post("/empreendimento/{emp_id}/dados/terreno/salvar")
async def salvar_terreno(emp_id: int, request: Request, cenario: Optional[int] = None,
                         s: Session = Depends(sessao),
                         autor: str = Depends(usuario_atual)):
    cen = _cenario_escolhido(s, emp_id, cenario)
    if cen["tipo"] == "orcado":
        raise HTTPException(409, "cenário congelado não aceita edição")
    f = await request.form()
    parcelas = [(edicao.num(v), edicao.data(d))
                for v, d in zip(f.getlist("valor"), f.getlist("vencimento"))]
    mudou = edicao.gravar_terreno(s, emp_id=emp_id, cenario_id=cen["id"],
                                  parcelas=parcelas, autor=autor)
    s.commit()
    return _voltar(emp_id, "terreno", mudou, cen)


@app.post("/empreendimento/{emp_id}/dados/obra/salvar")
async def salvar_obra(emp_id: int, request: Request, s: Session = Depends(sessao),
                      autor: str = Depends(usuario_atual)):
    f = await request.form()
    orcamento_id = edicao.inteiro(f.get("orcamento_id"))
    curva: list = []

    # duas maneiras de dar a curva: gerar por formato, ou digitar mês a mês
    if f.get("acao") == "gerar":
        inicio = edicao.data(f.get("inicio_obra"))
        meses = edicao.inteiro(f.get("meses_obra"), 36)
        formato = f.get("formato_curva") or "s_suave"
        if not inicio or meses < 1:
            raise HTTPException(422, "informe o início e a duração da obra")
        curva = [(somar_meses(inicio, i), fracao)
                 for i, fracao in enumerate(curva_de(formato, meses))]
    else:
        for mes, perc in zip(f.getlist("mes"), f.getlist("perc")):
            d = edicao.data(mes)
            if d:
                curva.append((edicao.fim_do_mes(d), edicao.num(perc)))

    soma = sum(p for _, p in curva)
    if curva and abs(soma - 1) > 1e-6:
        raise HTTPException(
            422, f"a curva soma {soma*100:.4f}% e precisa somar 100%. A curva "
                 f"distribui o custo no tempo — ela não pode mudar o custo.")

    try:
        mudou = edicao.gravar_obra(s, emp_id=emp_id, orcamento_id=orcamento_id,
                                   custo_raso=edicao.num(f.get("custo_raso")),
                                   versao=edicao.texto(f.get("versao")),
                                   curva=curva, autor=autor,
                                   substituir_eap=f.get("substituir_eap") == "1")
        s.commit()
    except ValueError as e:                         # recusa nossa, com explicação
        s.rollback()
        raise HTTPException(422, str(e))
    except Exception as e:                          # noqa: BLE001
        s.rollback()
        raise HTTPException(422, f"o banco recusou: {e}")
    return _voltar(emp_id, "obra", mudou, None)


@app.post("/empreendimento/{emp_id}/dados/unidades/salvar")
async def salvar_unidade(emp_id: int, request: Request,
                         s: Session = Depends(sessao),
                         autor: str = Depends(usuario_atual)):
    f = await request.form()
    try:
        mudou = edicao.gravar_unidade(s, emp_id=emp_id,
                                      unidade_id=edicao.inteiro(f.get("unidade_id")),
                                      enviado=dict(f), autor=autor)
        s.commit()
    except Exception as e:                          # noqa: BLE001
        s.rollback()
        raise HTTPException(422, f"o banco recusou: {e}")
    return _voltar(emp_id, "unidades", mudou, None)


# ================================================================ guia
@app.get("/guia", response_class=HTMLResponse)
def guia(request: Request):
    """O manual de preenchimento. Não toca no banco — é texto e só."""
    return templates.TemplateResponse(request, "guia.html", {"secoes": SECOES})


# ================================================================ estudo novo
INDICES = ["INCC-DI", "IGP-M", "IPCA"]


def _num(v, padrao=0.0):
    """Aceita vírgula decimal — é assim que se digita 0,045 num teclado daqui."""
    if v is None or str(v).strip() == "":
        return padrao
    try:
        return float(str(v).strip().replace(".", "").replace(",", ".")) \
            if "," in str(v) else float(v)
    except ValueError:
        return padrao


def _data(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def _tela_novo(request: Request, d: Estudo, erros=None, status=200):
    return templates.TemplateResponse(
        request, "novo.html",
        {"d": d, "erros": erros or [], "formatos": FORMATOS_CURVA,
         "indices": INDICES},
        status_code=status)


@app.get("/novo", response_class=HTMLResponse)
def tela_novo(request: Request):
    return _tela_novo(request, Estudo())


@app.post("/novo", response_class=HTMLResponse)
async def novo(request: Request, s: Session = Depends(sessao)):
    """
    Lê o formulário inteiro, valida tudo de uma vez e cria o estudo.

    A validação devolve a lista completa de problemas em vez de parar no
    primeiro: quem está preenchendo 40 campos merece ver os quatro erros juntos,
    não descobrir um por recarregamento.
    """
    f = await request.form()
    g = f.get

    parcelas = []
    for i in range(1, 7):
        valor = _num(g(f"terreno_valor_{i}"))
        if valor > 0:
            parcelas.append((valor, _data(g(f"terreno_venc_{i}"))))

    d = Estudo(
        nome=(g("nome") or "").strip(),
        sienge_enterprise_id=int(_num(g("sienge_enterprise_id"))) or None,
        area_privativa=_num(g("area_privativa")),
        area_construida=_num(g("area_construida")),
        data_lancamento=_data(g("data_lancamento")),
        data_entrega_prevista=_data(g("data_entrega_prevista")),
        unidades=int(_num(g("unidades"))),
        preco_m2=_num(g("preco_m2_estoque")),
        comissao=_num(g("comissao")), ato=_num(g("ato")),
        mensais=_num(g("mensais")), anuais=_num(g("anuais")),
        semestrais=_num(g("semestrais")), unica=_num(g("unica")),
        chaves=_num(g("chaves")), n_mensais=int(_num(g("n_mensais"), 60)),
        inicio_vendas=_data(g("inicio_vendas")),
        unidades_por_mes=_num(g("unidades_por_mes"), 4),
        custo_raso=_num(g("custo_raso")),
        inicio_obra=_data(g("inicio_obra")),
        meses_obra=int(_num(g("meses_obra"), 36)),
        formato_curva=g("formato_curva") or "s_suave",
        taxa_adm_obra=_num(g("taxa_adm_obra")),
        taxa_viabilizacao=_num(g("taxa_viabilizacao")),
        outras_desp_adm_perc=_num(g("outras_desp_adm_perc")),
        ret=_num(g("ret")), distratos=_num(g("distratos")),
        despesas_comerciais=_num(g("despesas_comerciais")),
        decoracao=_num(g("decoracao")),
        projetos_e_outros=_num(g("projetos_e_outros")),
        marketing_stand=_num(g("marketing_stand")),
        marketing_propaganda=_num(g("marketing_propaganda")),
        outras_entradas=_num(g("outras_entradas")),
        terreno_parcelas=parcelas,
        terreno_registro_perc=_num(g("terreno_registro_perc")),
        indice_ate_chaves=g("indice_ate_chaves") or "",
        indice_apos_chaves=g("indice_apos_chaves") or "",
        indice_projetado_aa=_num(g("indice_projetado_aa")),
        corrigir_custo_obra=g("corrigir_custo_obra") != "0",
        tma_anual=_num(g("tma_anual"), 0.18),
        meses_pos_chaves=int(_num(g("meses_pos_chaves"), 6)),
    )

    erros = d.erros()
    if erros:
        return _tela_novo(request, d, erros, status=422)

    try:
        emp = criar_estudo(s, d)
    except Exception as e:                        # noqa: BLE001 — a tela mostra
        s.rollback()
        return _tela_novo(request, d, [f"O banco recusou o cadastro: {e}"], 422)

    # primeira rodada: o estudo já nasce calculado
    try:
        cen = q(s, """SELECT id FROM cenario
                       WHERE empreendimento_id = :e AND principal""", e=emp)[0]["id"]
        rodar_e_persistir(s, cen, executada_por="cadastro pela tela")
    except Exception as e:                        # noqa: BLE001
        return _tela_novo(
            request, d,
            [f"O cadastro foi criado, mas o primeiro cálculo falhou: {e}. "
             f"Abra o empreendimento e clique em Recalcular."], 422)

    return RedirectResponse(f"/empreendimento/{emp}", status_code=303)


# ================================================================ carga inicial
def _tem_empreendimento(s: Session) -> bool:
    return q(s, "SELECT count(*) AS n FROM empreendimento")[0]["n"] > 0


@app.get("/admin/carga", response_class=HTMLResponse)
def tela_carga(request: Request, s: Session = Depends(sessao)):
    return templates.TemplateResponse(request, "carga.html",
                                      {"ja_tem": _tem_empreendimento(s)})


@app.post("/admin/carga", response_class=HTMLResponse)
async def rodar_carga(request: Request, planilha: UploadFile = File(...),
                      parametros: UploadFile = File(...),
                      s: Session = Depends(sessao)):
    """
    Roda a migração inicial com os dois arquivos enviados.

    Os arquivos vão para um diretório temporário e são apagados no fim: a
    planilha e o JSON de parâmetros têm informação financeira da empresa e não
    ficam no disco do servidor depois que os dados já estão no banco.

    O `print` da migração é capturado e devolvido na tela — é o mesmo registro
    que apareceria no terminal, e é o que permite conferir contagem por contagem
    o que entrou.
    """
    import contextlib
    import shutil
    import tempfile

    import migrar_kiev

    pasta = tempfile.mkdtemp(prefix="carga-")
    saida, erro, emp_id = io.StringIO(), None, None
    try:
        caminhos = {}
        for campo, enviado in (("xlsx", planilha), ("json", parametros)):
            destino = pathlib.Path(pasta) / f"entrada.{campo}"
            destino.write_bytes(await enviado.read())
            caminhos[campo] = str(destino)
        try:
            with contextlib.redirect_stdout(saida):
                emp_id = migrar_kiev.main(caminhos["xlsx"], caminhos["json"])
        except Exception as e:                      # noqa: BLE001 — a tela mostra
            erro = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

    return templates.TemplateResponse(
        request, "carga.html",
        {"ja_tem": False, "registro": saida.getvalue(), "erro": erro,
         "emp_id": emp_id},
        status_code=500 if erro else 200)


# ================================================================ telas
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request, s: Session = Depends(sessao)):
    emps = q(s, """
        SELECT e.*,
               (SELECT count(*) FROM unidade u
                 WHERE u.empreendimento_id = e.id AND u.considerar_na_viabilidade) AS unidades,
               (SELECT count(*) FROM unidade u
                 WHERE u.empreendimento_id = e.id AND u.considerar_na_viabilidade
                   AND u.situacao IN ('Vendida','Permuta')) AS vendidas,
               r.id AS rodada_id,
               i.vgv, i.lucro, i.margem, i.exposicao_maxima, i.mes_exposicao,
               i.vpl, i.mtir_anual, r.executada_em
          FROM empreendimento e
          LEFT JOIN LATERAL (
                SELECT r.* FROM rodada r JOIN cenario c ON c.id = r.cenario_id
                 WHERE c.empreendimento_id = e.id AND c.principal
                 ORDER BY r.executada_em DESC LIMIT 1) r ON true
          LEFT JOIN indicador i ON i.rodada_id = r.id
         ORDER BY e.nome
    """)

    # A curva de caixa em miniatura no cartão. Vem do fluxo já gravado — não
    # recalcula nada — e serve para reconhecer a SPE sem ler número nenhum:
    # a forma do vale diz mais rápido do que "exposição máxima R$ 60,2 M".
    for e in emps:
        e["curva"] = _curva_de_caixa(s, e.get("rodada_id"))
        e["perc_vendido"] = (e["vendidas"] / e["unidades"]) if e["unidades"] else 0.0

    return templates.TemplateResponse(request, "inicio.html",
                                      {"empreendimentos": emps})


def _curva_de_caixa(s: Session, rodada_id, largura: float = 260,
                    altura: float = 46) -> Optional[dict]:
    """Saldo acumulado do fluxo gravado, já como caminho SVG."""
    if not rodada_id:
        return None
    linhas = q(s, """SELECT mes, sum(valor) AS mov FROM fluxo_projetado
                      WHERE rodada_id = :r GROUP BY mes ORDER BY mes""", r=rodada_id)
    if len(linhas) < 2:
        return None

    saldo, acumulado = [], 0.0
    for l in linhas:
        acumulado += float(l["mov"])
        saldo.append(acumulado)

    topo, fundo = max(saldo + [0.0]), min(saldo + [0.0])
    amplitude = (topo - fundo) or 1.0
    passo = largura / (len(saldo) - 1)

    def y(v: float) -> float:
        return altura - (v - fundo) / amplitude * altura

    pontos = [(i * passo, y(v)) for i, v in enumerate(saldo)]
    linha = "M" + " L".join(f"{x:.1f},{p:.1f}" for x, p in pontos)
    base = y(0.0)
    area = (f"{linha} L{largura:.1f},{base:.1f} L0,{base:.1f} Z")
    return {"linha": linha, "area": area, "zero": round(base, 1),
            "w": largura, "h": altura,
            "inicio": linhas[0]["mes"], "fim": linhas[-1]["mes"],
            "pior": min(saldo)}


def cenario_desatualizado(s: Session, cenario_id: int) -> bool:
    """A última rodada foi calculada com as premissas que estão valendo agora?"""
    from .servico import _hash
    ultima = q(s, """SELECT hash_entradas, congelada FROM rodada
                      WHERE cenario_id = :c ORDER BY executada_em DESC LIMIT 1""",
               c=cenario_id)
    if not ultima:
        return True
    if ultima[0]["congelada"]:
        return False          # congelado não recalcula, por definição
    try:
        return _hash(carregar_entradas(s, cenario_id)) != ultima[0]["hash_entradas"]
    except Exception:
        return True


def _empreendimento(s: Session, emp_id: int) -> dict:
    r = q(s, "SELECT * FROM empreendimento WHERE id = :e", e=emp_id)
    if not r:
        raise HTTPException(404, "empreendimento não encontrado")
    return r[0]


@app.get("/empreendimento/{emp_id}", response_class=HTMLResponse)
def tela_viabilidade(emp_id: int, request: Request, ate: Optional[str] = None,
                     s: Session = Depends(sessao)):
    emp = _empreendimento(s, emp_id)
    corte = dt.date.fromisoformat(ate) if ate else emp.get("mes_corte_realizado")
    linhas = visao_viabilidade(s, emp_id, corte)

    ind = q(s, """
        SELECT i.*, c.nome AS cenario, r.executada_em
          FROM indicador i
          JOIN rodada r ON r.id = i.rodada_id
          JOIN cenario c ON c.id = r.cenario_id
         WHERE c.empreendimento_id = :e AND c.principal
         ORDER BY r.executada_em DESC LIMIT 1
    """, e=emp_id)
    pendentes = q(s, """
        SELECT count(*) n, sum(m.valor) v FROM movimento_realizado m
          JOIN conta c ON c.id = m.conta_id
         WHERE m.empreendimento_id = :e AND c.linha_dre = 'A CLASSIFICAR'
    """, e=emp_id)[0]

    return templates.TemplateResponse(request, "viabilidade.html", {
        "emp": emp, "linhas": linhas,
        "ind": ind[0] if ind else None, "corte": corte, "pendentes": pendentes,
        "aba": "viabilidade",
    })


@app.get("/empreendimento/{emp_id}/cenarios", response_class=HTMLResponse)
def tela_cenarios(emp_id: int, request: Request, s: Session = Depends(sessao)):
    emp = _empreendimento(s, emp_id)
    cenarios = q(s, """
        SELECT c.id, c.nome, c.tipo, c.principal, c.mes_base,
               c.indice_ate_chaves, c.indice_apos_chaves,
               (SELECT valor FROM premissa
                 WHERE cenario_id = c.id AND chave = 'indice_projetado_aa') AS indice_aa,
               i.vgv, i.receita_liquida, i.lucro, i.margem, i.custo_m2_privativa,
               i.preco_m2_vgv, i.exposicao_maxima, i.mes_exposicao, i.vpl,
               i.tir_anual, i.mtir_anual, i.aporte_necessario, r.executada_em,
               (SELECT preco_m2 FROM preco_cenario
                 WHERE cenario_id = c.id AND tipo_venda = 'Normal') AS preco_m2,
               (SELECT sum(quantidade) FROM plano_venda WHERE cenario_id = c.id) AS unidades_plano
          FROM cenario c
          LEFT JOIN LATERAL (
                SELECT * FROM rodada WHERE cenario_id = c.id
                 ORDER BY executada_em DESC LIMIT 1) r ON true
          LEFT JOIN indicador i ON i.rodada_id = r.id
         WHERE c.empreendimento_id = :e
         ORDER BY c.tipo DESC, i.lucro DESC NULLS LAST
    """, e=emp_id)

    metricas = [
        ("VGV", "vgv", "moeda_m"), ("Receita líquida", "receita_liquida", "moeda_m"),
        ("Lucro", "lucro", "moeda_m"), ("Margem", "margem", "perc"),
        ("Preço médio do VGV", "preco_m2_vgv", "m2"),
        ("Custo raso por m²", "custo_m2_privativa", "m2"),
        ("Exposição máxima de caixa", "exposicao_maxima", "moeda_m"),
        ("Aporte necessário", "aporte_necessario", "moeda_m"),
        ("VPL", "vpl", "moeda_m"), ("TIR", "tir_anual", "perc"),
        ("MTIR", "mtir_anual", "perc"),
    ]
    # o indicador guardado vale para as premissas de quando a rodada correu.
    # Se alguém mexeu depois, a tela precisa dizer — senão mostra o índice novo
    # ao lado de um número calculado sem ele.
    for c in cenarios:
        c["desatualizado"] = cenario_desatualizado(s, c["id"])

    return templates.TemplateResponse(request, "cenarios.html", {
        "emp": emp, "cenarios": cenarios,
        "metricas": metricas, "aba": "cenarios",
        "algum_desatualizado": any(c["desatualizado"] for c in cenarios),
    })


@app.get("/empreendimento/{emp_id}/fluxo", response_class=HTMLResponse)
def tela_fluxo(emp_id: int, request: Request, cenario: Optional[int] = None,
               s: Session = Depends(sessao)):
    emp = _empreendimento(s, emp_id)
    cenarios = q(s, """SELECT id, nome, tipo, principal FROM cenario
                        WHERE empreendimento_id = :e ORDER BY tipo DESC, nome""",
                 e=emp_id)
    if not cenarios:
        raise HTTPException(404, "empreendimento sem cenário cadastrado")
    escolhido = cenario or next((c["id"] for c in cenarios if c["principal"]),
                                cenarios[0]["id"])

    entradas = carregar_entradas(s, escolhido)
    dre, fluxo, ind = calcular(entradas)

    receita = fluxo.receita_bruta()
    ded = fluxo.serie(("DEDUCAO",))
    gasto = fluxo.serie(("GASTO", "FINANCEIRO"))
    mov = fluxo.movimento()
    saldo = fluxo.saldo_caixa()

    meses = [m for m in fluxo.meses if receita[m] or gasto[m] or ded[m]]
    if not meses:
        meses = fluxo.meses[:12]
    ultimo = max(fluxo.meses.index(m) for m in meses)
    meses = fluxo.meses[:ultimo + 2]

    serie = [{
        "mes": m, "entrada": receita[m], "saida": gasto[m] + ded[m],
        "movimento": mov[m], "saldo": saldo[m],
    } for m in meses]

    obra = fluxo.linha("OBRA").valores
    por_ano: dict[int, dict] = {}
    for l in serie:
        a = por_ano.setdefault(l["mes"].year, {"entrada": 0.0, "saida": 0.0,
                                               "obra": 0.0, "saldo": 0.0})
        a["entrada"] += l["entrada"]
        a["saida"] += l["saida"]
        a["obra"] += obra.get(l["mes"], 0.0)
        a["saldo"] = l["saldo"]

    return templates.TemplateResponse(request, "fluxo.html", {
        "emp": emp, "cenarios": cenarios, "premissas": entradas.premissas,
        "escolhido": escolhido, "serie": serie, "por_ano": sorted(por_ano.items()),
        "ind": ind, "dre": dre, "aba": "fluxo",
        "grafico_mov": _grafico_barras(serie),
        "grafico_saldo": _grafico_saldo(serie, ind),
    })


@app.get("/empreendimento/{emp_id}/importar", response_class=HTMLResponse)
def tela_importar(emp_id: int, request: Request, s: Session = Depends(sessao)):
    emp = _empreendimento(s, emp_id)
    historico = q(s, """SELECT * FROM importacao WHERE empreendimento_id = :e
                         ORDER BY importado_em DESC LIMIT 30""", e=emp_id)
    return templates.TemplateResponse(request, "importar.html", {
        "emp": emp, "historico": historico, "aba": "importar",
    })


# ================================================================ ações
FONTES = {
    "unidades": (normalizar_unidades, gravar.gravar_unidades),
    "contratos": (normalizar_contratos, gravar.gravar_contratos),
    "receber": (normalizar_receber, gravar.gravar_receber),
    "recebido": (normalizar_recebido, gravar.gravar_recebido),
    "fin_obra": (normalizar_movimentos, gravar.gravar_movimentos),
}


@app.post("/empreendimento/{emp_id}/importar")
async def importar(emp_id: int, fonte: str = Form(...), aba: str = Form(""),
                   arquivo: UploadFile = File(...), s: Session = Depends(sessao)):
    if fonte not in FONTES:
        raise HTTPException(400, f"fonte desconhecida: {fonte}")
    normalizar, gravar_fn = FONTES[fonte]
    conteudo = await arquivo.read()
    linhas = ler_planilha(io.BytesIO(conteudo), aba or None)
    gravar_fn(s, emp_id, normalizar(linhas), origem="upload",
              arquivo=arquivo.filename)
    return RedirectResponse(f"/empreendimento/{emp_id}/importar", status_code=303)


@app.post("/empreendimento/{emp_id}/recalcular")
def recalcular(emp_id: int, s: Session = Depends(sessao)):
    ids = [c["id"] for c in q(s, """SELECT id FROM cenario
                                     WHERE empreendimento_id = :e""", e=emp_id)]
    for cid in ids:
        rodar_e_persistir(s, cid, executada_por="tela", forcar=True)
    return RedirectResponse(f"/empreendimento/{emp_id}", status_code=303)


# ================================================================ API
@app.get("/api/empreendimentos")
def api_empreendimentos(s: Session = Depends(sessao)):
    return q(s, "SELECT * FROM empreendimento ORDER BY nome")


@app.get("/api/empreendimentos/{emp_id}/viabilidade")
def api_viabilidade(emp_id: int, ate: Optional[str] = None,
                    s: Session = Depends(sessao)):
    corte = dt.date.fromisoformat(ate) if ate else None
    return {"empreendimento": _empreendimento(s, emp_id),
            "linhas": visao_viabilidade(s, emp_id, corte)}


@app.get("/api/cenarios/{cenario_id}/fluxo")
def api_fluxo(cenario_id: int, s: Session = Depends(sessao)):
    dre, fluxo, ind = calcular(carregar_entradas(s, cenario_id))
    saldo = fluxo.saldo_caixa()
    mov = fluxo.movimento()
    return {
        "dre": {k: v for k, v in dre.__dict__.items()},
        "indicadores": ind.__dict__,
        "fluxo": [{"mes": m.isoformat(), "movimento": round(mov[m], 2),
                   "saldo": round(saldo[m], 2)} for m in fluxo.meses],
    }


@app.post("/api/cenarios/{cenario_id}/rodar")
def api_rodar(cenario_id: int, s: Session = Depends(sessao)):
    return {"rodada_id": rodar_e_persistir(s, cenario_id, "api", forcar=True)}


@app.get("/saude")
def saude(s: Session = Depends(sessao)):
    return {"ok": True, "empreendimentos": q(s, "SELECT count(*) n FROM empreendimento")[0]["n"]}


# ================================================================ gráficos
# Paleta validada pelo validate_palette.js do skill de dataviz:
#   claro  #0d9080 / #c04a1e sobre #EFEEE8 — todas as checagens passam
#   escuro #35a897 / #d96a42 sobre #181C19 — todas as checagens passam
L, A, T, B = 44, 16, 26, 30          # margens do desenho


def _grafico_barras(serie: list[dict], largura=980, altura=250) -> dict:
    """Movimento mensal: entradas acima do zero, saídas abaixo. Duas séries."""
    if not serie:
        return {"barras": [], "zero": 0, "w": largura, "h": altura, "eixo": []}
    teto = max([l["entrada"] for l in serie] + [0]) or 1
    piso = min([l["saida"] for l in serie] + [0]) or -1
    amplitude = teto - piso
    plot_h = altura - T - B
    zero = T + plot_h * (teto / amplitude)
    passo = (largura - L - A) / len(serie)
    lg = max(passo * 0.62, 1.2)

    barras = []
    for i, l in enumerate(serie):
        x = L + passo * i + (passo - lg) / 2
        h_ent = plot_h * (l["entrada"] / amplitude)
        h_sai = plot_h * (abs(l["saida"]) / amplitude)
        barras.append({
            "x": round(x, 2), "lg": round(lg, 2),
            "y_ent": round(zero - h_ent, 2), "h_ent": round(max(h_ent, 0), 2),
            "y_sai": round(zero + 1, 2), "h_sai": round(max(h_sai - 1, 0), 2),
            "cx": round(x + lg / 2, 2), **l,
        })
    return {"barras": barras, "zero": round(zero, 2), "w": largura, "h": altura,
            "escala": _escala(piso, teto, T, plot_h, amplitude),
            "rotulos": _rotulos_x(serie, L, passo)}


def _grafico_saldo(serie: list[dict], ind, largura=980, altura=230) -> dict:
    """Saldo acumulado: uma série só, com o vale marcado."""
    if not serie:
        return {"linha": "", "w": largura, "h": altura}
    valores = [l["saldo"] for l in serie]
    teto, piso = max(valores + [0]), min(valores + [0])
    amplitude = (teto - piso) or 1
    plot_h = altura - T - B
    y = lambda v: T + plot_h * ((teto - v) / amplitude)
    passo = (largura - L - A) / max(len(serie) - 1, 1)
    pontos = [(round(L + passo * i, 2), round(y(l["saldo"]), 2))
              for i, l in enumerate(serie)]

    zero = round(y(0), 2)
    area = (f"M {pontos[0][0]},{zero} "
            + " ".join(f"L {x},{yy}" for x, yy in pontos)
            + f" L {pontos[-1][0]},{zero} Z")
    i_vale = valores.index(min(valores))
    return {
        "linha": "M " + " L ".join(f"{x},{yy}" for x, yy in pontos),
        "area": area, "zero": zero, "w": largura, "h": altura,
        "pontos": [{"x": p[0], "y": p[1], **serie[i]} for i, p in enumerate(pontos)],
        "vale": {"x": pontos[i_vale][0], "y": pontos[i_vale][1],
                 "valor": valores[i_vale], "mes": serie[i_vale]["mes"]},
        "escala": _escala(piso, teto, T, plot_h, amplitude),
        "rotulos": _rotulos_x(serie, L, passo),
    }


def _escala(piso, teto, topo, plot_h, amplitude, n=4):
    saida = []
    for k in range(n + 1):
        v = piso + (teto - piso) * k / n
        saida.append({"y": round(topo + plot_h * ((teto - v) / amplitude), 2),
                      "rotulo": f"{v/1_000_000:,.0f}".replace(".", ",")})
    return saida


def _rotulos_x(serie, esquerda, passo):
    """Um rótulo por ano, no primeiro mês em que ele aparece."""
    saida, visto = [], set()
    for i, l in enumerate(serie):
        if l["mes"].year not in visto:
            visto.add(l["mes"].year)
            saida.append({"x": round(esquerda + passo * i, 2), "texto": str(l["mes"].year)})
    return saida
