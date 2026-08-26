"""
O cronograma dentro do estudo: gravar os marcos e explicar o que eles mudam.

Este módulo é a ponte entre `pdp.py`, que sabe ler o PDP, e o resto do sistema,
que sabe calcular. Ele grava, conta o que gravou, e responde à pergunta que o
usuário faz na tela: *o que muda no meu caixa por causa disso?*

Uma decisão de projeto que vale explicitar: **sincronizar substitui**. Não há
merge, não há "manter o que foi editado aqui". Quem manda no cronograma é o
PDP; se o estudo pudesse divergir, em duas semanas ninguém saberia qual das
duas datas é a verdadeira.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from .db import q
from .pdp import PDP, MarcoPDP, PDPIndisponivel, _critico, _data


# =====================================================================
# gravação
# =====================================================================
def gravar_marcos(s: Session, emp_id: int, marcos: list[MarcoPDP]) -> tuple[int, int]:
    """
    Substitui o cronograma do empreendimento. Devolve (marcos, dependências).

    Os itens de composição amarrados a marcos são preservados pelo `pdp_id`:
    apagar e recriar a linha do marco perderia a amarração, então a gravação é
    um upsert pela chave (empreendimento, pdp_id), e só some o marco que
    realmente deixou de existir no PDP.
    """
    vistos = []
    for m in marcos:
        r = q(s, """
            INSERT INTO marco (empreendimento_id, pdp_id, nome, area_codigo,
                               processo, fase, inicio, fim, duracao, progresso,
                               critico, sincronizado_em)
            VALUES (:e, :p, :n, :a, :pr, :f, :i, :fim, :d, :g, :c, now())
            ON CONFLICT (empreendimento_id, pdp_id) DO UPDATE SET
              nome = EXCLUDED.nome, area_codigo = EXCLUDED.area_codigo,
              processo = EXCLUDED.processo, fase = EXCLUDED.fase,
              inicio = EXCLUDED.inicio, fim = EXCLUDED.fim,
              duracao = EXCLUDED.duracao, progresso = EXCLUDED.progresso,
              critico = EXCLUDED.critico, sincronizado_em = now()
            RETURNING id
        """, e=emp_id, p=m.pdp_id, n=m.nome, a=m.area_codigo, pr=m.processo,
             f=m.fase, i=m.inicio, fim=m.fim, d=m.duracao, g=m.progresso,
             c=m.critico)[0]["id"]
        vistos.append(m.pdp_id)
        q(s, "DELETE FROM marco_dependencia WHERE marco_id = :m", m=r)
        for pred, tipo, lag in m.predecessores:
            q(s, """INSERT INTO marco_dependencia (marco_id, predecessor, tipo, defasagem)
                    VALUES (:m, :p, :t, :l) ON CONFLICT DO NOTHING""",
              m=r, p=pred, t=tipo, l=lag)

    if vistos:
        q(s, """DELETE FROM marco WHERE empreendimento_id = :e
                  AND pdp_id <> ALL(:v)""", e=emp_id, v=vistos)

    n_dep = q(s, """SELECT count(*) AS n FROM marco_dependencia d
                      JOIN marco m ON m.id = d.marco_id
                     WHERE m.empreendimento_id = :e""", e=emp_id)[0]["n"]
    return len(vistos), int(n_dep)


def sincronizar(s: Session, emp_id: int, autor: str) -> dict:
    """
    Lê o PDP e grava. Nunca levanta: devolve o resultado, bom ou ruim.

    Quem chama é uma tela, e uma tela precisa contar o que aconteceu — inclusive
    quando o que aconteceu foi o PDP estar fora do ar.
    """
    emp = q(s, "SELECT id, nome, pdp_project_id FROM empreendimento WHERE id = :e",
            e=emp_id)[0]
    if not emp["pdp_project_id"]:
        msg = ("este empreendimento ainda não tem o código do projeto no PDP. "
               "Preencha-o no cadastro — é o número que aparece em project_id "
               "na URL do PDP.")
        _registrar(s, emp_id, autor, False, 0, 0, msg)
        return {"ok": False, "mensagem": msg}

    try:
        with PDP() as pdp:
            marcos = pdp.marcos(int(emp["pdp_project_id"]))
        n, d = gravar_marcos(s, emp_id, marcos)
    except PDPIndisponivel as e:
        _registrar(s, emp_id, autor, False, 0, 0, str(e))
        return {"ok": False, "mensagem": str(e)}
    except Exception as e:                                    # noqa: BLE001
        msg = f"a leitura do PDP falhou de um jeito inesperado: {e}"
        _registrar(s, emp_id, autor, False, 0, 0, msg)
        return {"ok": False, "mensagem": msg}

    sem_area = sum(1 for m in marcos if not m.area_codigo)
    msg = f"{n} marcos e {d} ligações de precedência."
    if sem_area:
        msg += (f" {sem_area} marco(s) vieram sem área e por isso não entram em "
                f"setor nenhum — confira a classificação deles no PDP.")
    _registrar(s, emp_id, autor, True, n, d, msg)
    return {"ok": True, "mensagem": msg, "marcos": n, "dependencias": d}


# =====================================================================
# a entrada por arquivo
# =====================================================================
class ArquivoInvalido(ValueError):
    """O arquivo não tem a cara de um cronograma exportado do PDP."""


def ler_arquivo(conteudo: bytes) -> list[MarcoPDP]:
    """
    Lê o cronograma exportado do PDP em JSON.

    O formato é o que a própria tela do PDP entrega, com um campo a mais: a
    área de cada marco, que o JSON do Gantt não traz e que é justamente o que
    liga o marco ao setor de custo.

        {"marcos": [{"id": "3272", "nome": "Matrículas Retificadas",
                     "area": "6", "processo": "Regularização",
                     "fase": "Projeto e Legalização",
                     "inicio": "03/06/2027", "fim": "02/09/2027",
                     "duracao": 66, "progresso": 0, "critico": false,
                     "predecessores": [["3221", "TI", 0]]}]}

    Erro de formato para a leitura. Um cronograma pela metade viraria uma curva
    de caixa errada sem ninguém perceber — é o mesmo cuidado da leitura direta.
    """
    import json
    try:
        dados = json.loads(conteudo.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ArquivoInvalido(f"o arquivo não é um JSON legível: {e}") from None

    marcos_brutos = dados.get("marcos") if isinstance(dados, dict) else dados
    if not isinstance(marcos_brutos, list) or not marcos_brutos:
        raise ArquivoInvalido(
            "não achei a lista de marcos no arquivo. Esperava um objeto com a "
            "chave 'marcos', ou uma lista direto.")

    marcos = []
    for i, m in enumerate(marcos_brutos, start=1):
        pid = str(m.get("id") or m.get("pdp_id") or "").lstrip("#").strip()
        nome = (m.get("nome") or m.get("text") or "").strip()
        if not pid or not nome:
            raise ArquivoInvalido(
                f"o marco na posição {i} está sem id ou sem nome. Nenhum marco "
                f"foi gravado — corrija o arquivo e importe de novo.")
        preds = []
        for pr in (m.get("predecessores") or []):
            if isinstance(pr, (list, tuple)) and pr:
                preds.append((str(pr[0]).lstrip("#"),
                              (pr[1] if len(pr) > 1 else "TI") or "TI",
                              int(pr[2]) if len(pr) > 2 and pr[2] else 0))
        marcos.append(MarcoPDP(
            pdp_id=pid, nome=nome,
            inicio=_data(m.get("inicio") or m.get("start_date")),
            fim=_data(m.get("fim") or m.get("end_date")),
            duracao=m.get("duracao") or m.get("real_duration") or m.get("duration"),
            progresso=int(m.get("progresso") or m.get("progress_percent") or 0),
            critico=bool(m.get("critico")) if isinstance(m.get("critico"), bool)
                    else _critico(m.get("critico")),
            area_codigo=str(m["area"]).strip() if m.get("area") not in (None, "") else None,
            processo=(m.get("processo") or None),
            fase=(m.get("fase") or None),
            predecessores=preds))
    return marcos


def importar(s: Session, emp_id: int, conteudo: bytes, autor: str) -> dict:
    """Mesma gravação da sincronização direta; só muda de onde vêm os marcos."""
    try:
        marcos = ler_arquivo(conteudo)
        n, d = gravar_marcos(s, emp_id, marcos)
    except ArquivoInvalido as e:
        _registrar(s, emp_id, autor, False, 0, 0, str(e))
        return {"ok": False, "mensagem": str(e)}
    except Exception as e:                                    # noqa: BLE001
        msg = f"a importação falhou: {e}"
        _registrar(s, emp_id, autor, False, 0, 0, msg)
        return {"ok": False, "mensagem": msg}

    sem_area = sum(1 for m in marcos if not m.area_codigo)
    msg = f"{n} marcos e {d} ligações, importados de arquivo."
    if sem_area:
        msg += (f" {sem_area} marco(s) vieram sem área e não entram em setor "
                f"nenhum.")
    _registrar(s, emp_id, autor, True, n, d, msg)
    return {"ok": True, "mensagem": msg}


def _registrar(s: Session, emp_id: int, autor: str, ok: bool,
               n: int, d: int, msg: str) -> None:
    q(s, """INSERT INTO sincronizacao_pdp
              (empreendimento_id, autor, ok, marcos, dependencias, mensagem)
            VALUES (:e, :a, :o, :n, :d, :m)""",
      e=emp_id, a=autor, o=ok, n=n, d=d, m=msg)


def ultima_sincronizacao(s: Session, emp_id: int) -> Optional[dict]:
    r = q(s, """SELECT * FROM sincronizacao_pdp WHERE empreendimento_id = :e
                 ORDER BY quando DESC LIMIT 1""", e=emp_id)
    return r[0] if r else None


# =====================================================================
# leitura para as telas
# =====================================================================
def por_area(s: Session, emp_id: int) -> list[dict]:
    """As áreas do PDP com os marcos deste empreendimento e o setor que pagam."""
    areas = q(s, """SELECT a.codigo, a.nome, a.setor, a.papel, a.ordem,
                           sc.nome AS setor_nome, sc.linha_dre
                      FROM area_pdp a
                      LEFT JOIN setor_custo sc ON sc.codigo = a.setor
                     ORDER BY a.ordem, a.nome""")
    marcos = q(s, """SELECT * FROM marco WHERE empreendimento_id = :e
                      ORDER BY fim, inicio, pdp_id""", e=emp_id)
    por = {}
    for m in marcos:
        por.setdefault(m["area_codigo"], []).append(m)

    saida = []
    for a in areas:
        ms = por.get(a["codigo"], [])
        if not ms:
            continue
        datas_i = [m["inicio"] for m in ms if m["inicio"]]
        datas_f = [m["fim"] for m in ms if m["fim"]]
        saida.append({**a, "marcos": ms, "n": len(ms),
                      "criticos": sum(1 for m in ms if m["critico"]),
                      "inicio": min(datas_i) if datas_i else None,
                      "fim": max(datas_f) if datas_f else None})
    return saida


def marcos_para_escolha(s: Session, emp_id: int, setor: str) -> list[dict]:
    """
    Os marcos que fazem sentido oferecer num item de composição deste setor.

    Primeiro os das áreas que alimentam o setor — são os candidatos naturais —
    e depois todos os outros, porque uma taxa pode muito bem estar amarrada a
    um marco de outra área (o alvará que a Engenharia espera, por exemplo).
    """
    return q(s, """
        SELECT m.id, m.pdp_id, m.nome, m.fim, m.critico,
               a.nome AS area_nome,
               (a.setor = :s) AS do_setor
          FROM marco m
          LEFT JOIN area_pdp a ON a.codigo = m.area_codigo
         WHERE m.empreendimento_id = :e
         ORDER BY (a.setor = :s) DESC NULLS LAST, m.fim, m.pdp_id
    """, e=emp_id, s=setor)


def resumo_do_setor(s: Session, emp_id: int, cenario_id: int, setor: str) -> dict:
    """Janela do setor no cronograma e quantos itens já têm data própria."""
    j = q(s, """SELECT min(m.inicio) AS inicio, max(m.fim) AS fim, count(*) AS marcos
                  FROM marco m JOIN area_pdp a ON a.codigo = m.area_codigo
                 WHERE m.empreendimento_id = :e AND a.setor = :s""",
          e=emp_id, s=setor)[0]
    i = q(s, """SELECT count(*) AS total, count(marco_id) AS com_marco
                  FROM composicao_item WHERE cenario_id = :c AND setor = :s""",
          c=cenario_id, s=setor)[0]
    return {**dict(j), **dict(i)}


# =====================================================================
# simulação: o atraso que anda pela rede
# =====================================================================
def propagar(marcos: list[dict], dependencias: dict[str, list],
             atrasos: dict[str, int]) -> dict[str, int]:
    """
    Dado um atraso em alguns marcos, quanto cada um dos outros anda.

    O estudo não recalcula cronograma — quem faz isso é o PDP. O que se faz
    aqui é mais modesto e é o que a pergunta pede: *se este marco escorregar
    tantos dias, quando passam a acontecer os que dependem dele?*

    A regra é a do caminho: um sucessor anda o máximo do que os seus
    predecessores andaram, e nunca menos que zero — adiantar um marco não puxa
    o resto para trás, porque quem vem depois tem os seus próprios motivos para
    começar quando começa. Por isso o resultado é sempre um deslocamento para a
    frente, e por isso ele é conservador de propósito.

    O tipo da ligação (TI, II, TT, IT) e a defasagem não mudam o quanto anda —
    mudam de qual ponta do predecessor a data do sucessor é medida, e essa conta
    é do PDP. Aqui interessa a propagação do atraso, que é a mesma para os
    quatro tipos.

    Devolve só quem se move, incluindo os marcos informados.
    """
    ordem = _ordem_topologica(marcos, dependencias)
    andou = {k: int(v) for k, v in atrasos.items() if int(v) > 0}
    for pdp_id in ordem:
        de_tras = [andou.get(pred, 0) for pred, _t, _l in dependencias.get(pdp_id, [])]
        herdado = max(de_tras, default=0)
        if herdado > andou.get(pdp_id, 0):
            andou[pdp_id] = herdado
    return {k: v for k, v in andou.items() if v > 0}


def _ordem_topologica(marcos: list[dict], dependencias: dict[str, list]) -> list[str]:
    """
    Os marcos numa ordem em que todo predecessor vem antes do seu sucessor.

    Se houver ciclo — e cronograma de obra tem, por engano de cadastro —, os
    marcos que sobram entram no fim pela data de início. Ciclo não pode travar
    a simulação inteira: o que ele faz é impedir a propagação naquele pedaço, e
    isso é melhor do que uma tela em branco.
    """
    ids = {str(m["pdp_id"]) for m in marcos}
    pendentes = {i: {p for p, _t, _l in dependencias.get(i, []) if p in ids} for i in ids}
    ordem, prontos = [], sorted(i for i, ps in pendentes.items() if not ps)
    vistos = set(prontos)
    while prontos:
        atual = prontos.pop(0)
        ordem.append(atual)
        for i, ps in pendentes.items():
            if atual in ps:
                ps.discard(atual)
                if not ps and i not in vistos:
                    vistos.add(i)
                    prontos.append(i)
        prontos.sort()
    resto = [i for i in ids if i not in vistos]
    if resto:
        por_data = {str(m["pdp_id"]): (m.get("inicio") or date.max) for m in marcos}
        ordem.extend(sorted(resto, key=lambda i: por_data.get(i, date.max)))
    return ordem


def dependencias_do_empreendimento(s: Session, emp_id: int) -> dict[str, list]:
    """{pdp_id do sucessor: [(pdp_id do predecessor, tipo, defasagem)]}"""
    linhas = q(s, """SELECT m.pdp_id, d.predecessor, d.tipo, d.defasagem
                       FROM marco_dependencia d
                       JOIN marco m ON m.id = d.marco_id
                      WHERE m.empreendimento_id = :e""", e=emp_id)
    saida: dict[str, list] = {}
    for l in linhas:
        saida.setdefault(l["pdp_id"], []).append(
            (l["predecessor"], l["tipo"], int(l["defasagem"] or 0)))
    return saida


def ajustes_do_cenario(s: Session, cenario_id: int) -> dict[int, int]:
    """{marco_id: dias} — o cronograma deslocado deste cenário."""
    return {l["marco_id"]: int(l["dias"]) for l in
            q(s, "SELECT marco_id, dias FROM cenario_marco_ajuste WHERE cenario_id = :c",
              c=cenario_id)}


# =====================================================================
# o que o Gantt precisa saber
# =====================================================================
def _fatia(marcos_da_area: int, setor: dict, total_composicao: float) -> float:
    """
    O custo que cada barra do Gantt carrega.

    Primeiro o que a casa informou: `custo_medio_marco` do setor. É um número
    de gente, não do sistema — uma taxa de cartório não custa o mesmo que um
    registro de incorporação, e nenhuma divisão automática sabe disso.

    Quando o setor ainda não informou, cai no rateio igual da composição, que é
    a mesma conta que o motor usa para distribuir o desembolso. A tela diz qual
    das duas está valendo.
    """
    if setor.get("custo_medio_marco") is not None:
        return float(setor["custo_medio_marco"])
    return (total_composicao / marcos_da_area) if marcos_da_area else 0.0


def dados_do_gantt(s: Session, emp_id: int, cenario_id: int) -> dict:
    """
    Tudo que a tela desenha, já resolvido do lado do servidor.

    Devolve os marcos com data, custo e área; os setores com o confronto entre
    a média informada e o total da composição; as ligações de precedência; e o
    marco de hoje. A tela não faz conta de dinheiro — ela desenha.
    """
    import datetime as dt

    hoje = dt.date.today()
    ajuste = ajustes_do_cenario(s, cenario_id)

    setores = {l["codigo"]: dict(l) for l in
               q(s, "SELECT * FROM setor_custo ORDER BY ordem")}
    totais = {l["setor"]: float(l["total"]) for l in
              q(s, """SELECT setor, sum(valor) AS total FROM composicao_item
                       WHERE cenario_id = :c GROUP BY setor""", c=cenario_id)}
    amarrados = {l["marco_id"]: float(l["total"]) for l in
                 q(s, """SELECT marco_id, sum(valor) AS total FROM composicao_item
                          WHERE cenario_id = :c AND marco_id IS NOT NULL
                          GROUP BY marco_id""", c=cenario_id)}

    linhas = q(s, """SELECT m.*, a.nome AS area_nome, a.setor, a.ordem AS area_ordem
                       FROM marco m
                       LEFT JOIN area_pdp a ON a.codigo = m.area_codigo
                      WHERE m.empreendimento_id = :e
                      ORDER BY a.ordem NULLS LAST, m.inicio, m.fim, m.pdp_id""",
                 e=emp_id)
    por_area = {}
    for l in linhas:
        por_area.setdefault(l["area_codigo"], []).append(l)

    marcos = []
    for l in linhas:
        d = ajuste.get(l["id"], 0)
        inicio = l["inicio"] + dt.timedelta(days=d) if l["inicio"] and d else l["inicio"]
        fim = l["fim"] + dt.timedelta(days=d) if l["fim"] and d else l["fim"]
        setor = setores.get(l["setor"]) if l["setor"] else None
        n_area = len(por_area.get(l["area_codigo"], []))
        custo = amarrados.get(l["id"])
        proprio = custo is not None
        if custo is None and setor:
            custo = _fatia(n_area, setor, totais.get(l["setor"], 0.0))
        marcos.append({
            "id": l["id"], "pdp_id": l["pdp_id"], "nome": l["nome"],
            "area": l["area_nome"], "area_codigo": l["area_codigo"],
            "setor": l["setor"], "setor_nome": setor["nome"] if setor else None,
            "inicio": inicio, "fim": fim, "duracao": l["duracao"],
            "progresso": l["progresso"], "critico": l["critico"],
            "deslocado": d,
            "custo": round(custo or 0.0, 2), "custo_proprio": proprio,
            "em_curso": bool(inicio and fim and inicio <= hoje <= fim),
            "concluido": bool(fim and fim < hoje) or l["progresso"] >= 100,
        })

    # o confronto que a tela mostra: a média informada bate com a composição?
    resumo_setores = []
    for cod, st in setores.items():
        n = sum(1 for m in marcos if m["setor"] == cod)
        if not n and not totais.get(cod):
            continue
        media = st.get("custo_medio_marco")
        resumo_setores.append({
            "codigo": cod, "nome": st["nome"], "marcos": n,
            "media": float(media) if media is not None else None,
            "composicao": totais.get(cod, 0.0),
            "pela_media": float(media) * n if media is not None else None,
        })

    deps = dependencias_do_empreendimento(s, emp_id)
    fol = folgas(marcos, deps)
    viz = vizinhos(marcos, deps)
    for m in marcos:
        f = fol.get(m["pdp_id"])
        m["folga_total"] = f["total"] if f else None
        m["folga_livre"] = f["livre"] if f else None
        m["antes"] = viz.get(m["pdp_id"], {}).get("antes", [])
        m["depois"] = viz.get(m["pdp_id"], {}).get("depois", [])

    # o caminho crítico que a folga aponta e o que o PDP marcou podem discordar:
    # dias corridos aqui, dias úteis lá. Mostrar a divergência é mais útil do
    # que escolher em silêncio qual das duas leituras vale.
    pela_folga = {m["pdp_id"] for m in marcos if m.get("folga_total") == 0}
    pelo_pdp = {m["pdp_id"] for m in marcos if m["critico"]}

    return {
        "marcos": marcos,
        "setores": resumo_setores,
        "dependencias": deps,
        "hoje": hoje,
        "atual": _marco_de_hoje(marcos, hoje),
        "ajustes": ajuste,
        "criticos_pdp": len(pelo_pdp),
        "criticos_folga": len(pela_folga),
        "divergentes": sorted(pela_folga ^ pelo_pdp),
    }


def _marco_de_hoje(marcos: list[dict], hoje) -> Optional[dict]:
    """
    Onde o projeto está agora.

    Preferência para o que está em curso e mais perto de terminar — é o que a
    obra chama de "frente atual". Sem nenhum em curso, o próximo a começar; e
    se tudo já passou, o último que terminou.
    """
    em_curso = [m for m in marcos if m["em_curso"]]
    if em_curso:
        return min(em_curso, key=lambda m: (m["fim"], not m["critico"]))
    futuros = [m for m in marcos if m["inicio"] and m["inicio"] > hoje]
    if futuros:
        return min(futuros, key=lambda m: m["inicio"])
    passados = [m for m in marcos if m["fim"]]
    return max(passados, key=lambda m: m["fim"]) if passados else None


def desembolso_por_periodo(s: Session, emp_id: int, cenario_id: int,
                           passo: str = "mes") -> list[dict]:
    """
    Quanto sai de caixa em cada período, e de quais setores.

    Vem dos mesmos pesos que o motor usa — não é uma segunda conta paralela.
    Se esta tela e o fluxo de caixa discordassem, uma das duas estaria mentindo.
    """
    from .repositorio import pesos_por_setor
    import datetime as dt

    pesos = pesos_por_setor(s, emp_id, cenario_id)
    totais = {l["setor"]: float(l["total"]) for l in
              q(s, """SELECT setor, sum(valor) AS total FROM composicao_item
                       WHERE cenario_id = :c GROUP BY setor""", c=cenario_id)}
    nomes = {l["codigo"]: l["nome"] for l in q(s, "SELECT codigo, nome FROM setor_custo")}

    def rotulo(m: dt.date) -> tuple:
        if passo == "ano":
            return (dt.date(m.year, 12, 31), str(m.year))
        if passo == "trimestre":
            t = (m.month - 1) // 3 + 1
            fim = dt.date(m.year, t * 3, 1)
            return (fim, f"{t}º tri {m.year}")
        return (m, f"{m.month:02d}/{m.year}")

    balde: dict = {}
    for setor, meses in pesos.items():
        total = totais.get(setor, 0.0)
        for mes, w in meses.items():
            chave, texto = rotulo(mes)
            b = balde.setdefault(chave, {"quando": chave, "rotulo": texto,
                                         "total": 0.0, "setores": {}})
            v = total * w
            b["total"] += v
            b["setores"][setor] = b["setores"].get(setor, 0.0) + v

    saida = []
    for chave in sorted(balde):
        b = balde[chave]
        b["setores"] = sorted(
            ({"setor": k, "nome": nomes.get(k, k), "valor": round(v, 2)}
             for k, v in b["setores"].items() if v > 0.005),
            key=lambda x: -x["valor"])
        b["total"] = round(b["total"], 2)
        saida.append(b)
    return saida


# =====================================================================
# a simulação virando cenário
# =====================================================================
def simular(s: Session, emp_id: int, cenario_id: int,
            atrasos: dict[str, int]) -> dict:
    """
    O efeito de um atraso, sem gravar nada: quem anda e quanto.

    Devolve as datas novas e o total deslocado, para a tela mostrar antes de
    alguém decidir se aquilo vira cenário.
    """
    import datetime as dt

    marcos = q(s, """SELECT id, pdp_id, nome, inicio, fim, critico
                       FROM marco WHERE empreendimento_id = :e""", e=emp_id)
    deps = dependencias_do_empreendimento(s, emp_id)
    base = ajustes_do_cenario(s, cenario_id)
    por_pdp = {m["pdp_id"]: m for m in marcos}

    partida = {m["pdp_id"]: base.get(m["id"], 0) for m in marcos}
    partida.update({k: int(v) for k, v in atrasos.items() if k in por_pdp})

    movidos = propagar(marcos, deps, partida)
    informados = {k for k, v in atrasos.items() if int(v) > 0}

    detalhe = []
    for pdp_id, dias in sorted(movidos.items(), key=lambda kv: -kv[1]):
        m = por_pdp.get(pdp_id)
        if not m or not m["fim"]:
            continue
        detalhe.append({
            "pdp_id": pdp_id, "nome": m["nome"], "critico": m["critico"],
            "dias": dias, "informado": pdp_id in informados,
            "fim_antes": m["fim"],
            "fim_depois": m["fim"] + dt.timedelta(days=dias),
        })
    return {
        "movidos": detalhe,
        "quantos": len(detalhe),
        "informados": len(informados),
        "arrastados": len(detalhe) - len(informados),
        "fim_antes": max((m["fim"] for m in marcos if m["fim"]), default=None),
        "fim_depois": max((d["fim_depois"] for d in detalhe),
                          default=max((m["fim"] for m in marcos if m["fim"]), default=None)),
    }


def salvar_como_cenario(s: Session, emp_id: int, base_id: int, nome: str,
                        atrasos: dict[str, int], autor: str) -> int:
    """
    Congela a simulação num cenário próprio, copiado do que a originou.

    Um cenário é a mesma conta com outras premissas. Aqui a premissa que muda é
    o calendário: mesmo preço, mesmo custo, mesma obra — outra data. Por isso a
    cópia leva tudo do cenário de origem e só acrescenta os deslocamentos.
    """
    base = q(s, "SELECT * FROM cenario WHERE id = :c", c=base_id)[0]
    novo = q(s, """
        INSERT INTO cenario (empreendimento_id, nome, tipo, mes_base,
                             horizonte_meses, principal,
                             indice_ate_chaves, indice_apos_chaves)
        VALUES (:e, :n, 'projecao', :mb, :h, false, :i1, :i2)
        RETURNING id""",
        e=emp_id, n=nome.strip()[:60] or "simulação de atraso",
        mb=base["mes_base"], h=base["horizonte_meses"],
        i1=base["indice_ate_chaves"], i2=base["indice_apos_chaves"])[0]["id"]

    for tabela, colunas in (
            ("premissa", "chave, valor, unidade, origem"),
            ("preco_cenario", "tipo_venda, preco_m2, preco_unidade, usar_tabela"),
            ("plano_venda", "mes, quantidade, tipo_venda"),
            ("premissa_terreno", "ordem, valor, vencimento"),
            ("composicao_item", "setor, ordem, descricao, quantidade, unidade, "
                                "valor_unitario, valor, observacao, marco_id")):
        q(s, f"""INSERT INTO {tabela} (cenario_id, {colunas})
                 SELECT :novo, {colunas} FROM {tabela} WHERE cenario_id = :base""",
          novo=novo, base=base_id)

    movidos = simular(s, emp_id, base_id, atrasos)["movidos"]
    informados = {k for k, v in atrasos.items() if int(v) > 0}
    ids = {m["pdp_id"]: m["id"] for m in
           q(s, "SELECT id, pdp_id FROM marco WHERE empreendimento_id = :e", e=emp_id)}
    for d in movidos:
        q(s, """INSERT INTO cenario_marco_ajuste (cenario_id, marco_id, dias, origem)
                VALUES (:c, :m, :d, :o)""",
          c=novo, m=ids[d["pdp_id"]], d=d["dias"],
          o="simulacao" if d["pdp_id"] in informados else "propagacao")

    q(s, """INSERT INTO alteracao (empreendimento_id, cenario_id, modulo, entidade,
                                   campo, valor_anterior, valor_novo, autor)
            VALUES (:e, :c, 'cronograma', 'cenário', 'criado a partir de simulação',
                    NULL, :v, :a)""",
      e=emp_id, c=novo, a=autor,
      v=f"{nome} — {len(informados)} marco(s) atrasado(s), "
        f"{len(movidos) - len(informados)} arrastado(s) pela rede")
    return novo


def para_json(dados: dict) -> str:
    """
    O mesmo pacote de dados, pronto para o desenho no navegador.

    Serializa datas em ISO e escapa `</` — o JSON vai dentro de uma tag
    `<script>`, e um nome de marco que contivesse `</script>` fecharia a tag e
    quebraria a página. Nomes vêm do PDP, então não são texto confiável.
    """
    import datetime as dt
    import json

    def conv(o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        raise TypeError(type(o))

    bruto = json.dumps(dados, default=conv, ensure_ascii=False)
    return bruto.replace("</", "<\\/").replace("<!--", "<\\!--")


# =====================================================================
# folga: quanto cada marco pode atrasar sem consequência
# =====================================================================
def dias_uteis(de, ate) -> int:
    """
    Quantos dias úteis separam duas datas. Negativo quando `ate` vem antes.

    Sábado e domingo não contam. Feriado conta — o sistema não tem calendário de
    feriados, e inventar um erraria mais do que ignorar. Para medir folga, o
    erro de alguns feriados é ruído; para prometer data, quem manda é o PDP.
    """
    import datetime as dt
    if ate < de:
        return -dias_uteis(ate, de)
    inteiras, resto = divmod((ate - de).days, 7)
    n = inteiras * 5
    d = de
    for _ in range(resto):
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def somar_uteis(data, n: int):
    """A data que fica `n` dias úteis depois (ou antes, se `n` for negativo)."""
    import datetime as dt
    passo = 1 if n >= 0 else -1
    faltam, d = abs(n), data
    while faltam:
        d += dt.timedelta(days=passo)
        if d.weekday() < 5:
            faltam -= 1
    return d


def folgas(marcos: list[dict], dependencias: dict[str, list]) -> dict[str, dict]:
    """
    Folga total e folga livre de cada marco, em **dias úteis**.

    **Folga total** é quanto o marco pode atrasar sem empurrar a entrega do
    projeto. **Folga livre** é quanto pode atrasar sem empurrar nem o sucessor
    mais apertado — sempre menor ou igual à total. Zero nas duas é caminho
    crítico: ali cada dia de atraso é um dia a mais no projeto.

    A conta é uma passagem de trás para frente sobre as datas que o PDP já
    publicou. O estudo não reprograma cronograma — mede a folga do que existe.
    A unidade é dia útil porque é a do PDP: em dias corridos, uma tarefa de 220
    dias úteis parece ter três meses de folga que não existem.

    Feriado não entra, então os números podem diferir dos do PDP por alguns
    dias. Por isso a tela mostra as duas leituras de caminho crítico — a do PDP
    e a da folga — e avisa quando discordam.
    """
    por_id = {str(m["pdp_id"]): m for m in marcos if m.get("inicio") and m.get("fim")}
    if not por_id:
        return {}

    sucessores: dict[str, list] = {}
    for alvo, lista in dependencias.items():
        for pred, tipo, lag in lista:
            if pred in por_id and alvo in por_id:
                sucessores.setdefault(pred, []).append((alvo, tipo, int(lag or 0)))

    fim_projeto = max(m["fim"] for m in por_id.values())
    dur = {k: dias_uteis(m["inicio"], m["fim"]) for k, m in por_id.items()}

    ordem = _ordem_topologica(list(por_id.values()), dependencias)
    lf: dict = {}

    for pdp_id in reversed([i for i in ordem if i in por_id]):
        limites = []
        for suc, tipo, lag in sucessores.get(pdp_id, []):
            if suc not in lf:
                continue
            ls_suc = somar_uteis(lf[suc], -dur[suc])
            if tipo == "TI":                      # término → início
                limites.append(somar_uteis(ls_suc, -(1 + lag)))
            elif tipo == "II":                    # início → início
                limites.append(somar_uteis(ls_suc, dur[pdp_id] - lag))
            elif tipo == "TT":                    # término → término
                limites.append(somar_uteis(lf[suc], -lag))
            else:                                 # IT — início → término
                limites.append(somar_uteis(lf[suc], dur[pdp_id] - lag))
        lf[pdp_id] = min(limites) if limites else fim_projeto

    saida = {}
    for pdp_id, m in por_id.items():
        total = dias_uteis(m["fim"], lf[pdp_id])
        livres = []
        for suc, tipo, lag in sucessores.get(pdp_id, []):
            s_ini, s_fim = por_id[suc]["inicio"], por_id[suc]["fim"]
            if tipo == "TI":
                livres.append(dias_uteis(m["fim"], s_ini) - 1 - lag)
            elif tipo == "II":
                livres.append(dias_uteis(m["inicio"], s_ini) - lag)
            elif tipo == "TT":
                livres.append(dias_uteis(m["fim"], s_fim) - lag)
            else:
                livres.append(dias_uteis(m["inicio"], s_fim) - lag)
        livre = min(livres) if livres else total
        saida[pdp_id] = {
            "total": max(total, 0),
            "livre": max(min(livre, total), 0),
            "limite": lf[pdp_id],
            "sem_folga": total <= 0,
        }
    return saida


def vizinhos(marcos: list[dict], dependencias: dict[str, list]) -> dict[str, dict]:
    """Para cada marco, quem vem antes e quem vem depois, com nome e tipo."""
    nomes = {str(m["pdp_id"]): m.get("nome", "") for m in marcos}
    saida = {k: {"antes": [], "depois": []} for k in nomes}
    for alvo, lista in dependencias.items():
        for pred, tipo, lag in lista:
            lag = int(lag or 0)
            if alvo in saida:
                saida[alvo]["antes"].append(
                    {"pdp_id": pred, "nome": nomes.get(pred, "(fora do recorte)"),
                     "tipo": tipo, "lag": lag})
            if pred in saida:
                saida[pred]["depois"].append(
                    {"pdp_id": alvo, "nome": nomes.get(alvo, ""), "tipo": tipo,
                     "lag": lag})
    return saida
