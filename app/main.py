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
from .repositorio import carregar_entradas
from .servico import calcular, rodar_e_persistir, visao_viabilidade

AQUI = pathlib.Path(__file__).resolve().parent
app = FastAPI(title="Viabilidade de Incorporação", docs_url="/api/docs")
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


templates.env.filters.update(moeda=moeda, milhoes=milhoes,
                             percentual=percentual, mes=mes_curto)


@app.on_event("startup")
def preparar():
    aplicar_migrations()


# ================================================================ telas
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request, s: Session = Depends(sessao)):
    emps = q(s, """
        SELECT e.*,
               (SELECT count(*) FROM unidade u
                 WHERE u.empreendimento_id = e.id AND u.considerar_na_viabilidade) AS unidades,
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
    return templates.TemplateResponse(request, "inicio.html",
                                      {"empreendimentos": emps})


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
    return templates.TemplateResponse(request, "cenarios.html", {
        "emp": emp, "cenarios": cenarios,
        "metricas": metricas, "aba": "cenarios",
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
        "emp": emp, "cenarios": cenarios,
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
