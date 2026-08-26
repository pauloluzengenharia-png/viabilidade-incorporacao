"""
Conferências dos setores de custo com composição própria.

A promessa destes módulos é curta e vale a pena travar num teste: **a linha do
resultado é a soma da composição, e nada além dela**. Não existe mais valor
solto escondido numa premissa, e um setor sem item nenhum vale zero — o que é
diferente de custar zero, e por isso a tela avisa.

Estes testes não dependem da Kiev: cada um monta a sua própria composição num
cenário de projeção, confere, e desfaz o que fez.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa de DATABASE_URL apontando para um banco migrado")

from app import edicao, glossario, procedencia          # noqa: E402
from app.db import Sessao, q                            # noqa: E402
from app.repositorio import SETORES_COMPOSTOS, carregar_entradas, somar_composicoes  # noqa: E402
from app.servico import LINHAS_DRE, calcular            # noqa: E402

CENT = 0.01


@pytest.fixture(scope="module")
def s():
    ses = Sessao()
    yield ses
    ses.close()


@pytest.fixture(scope="module")
def cenario(s):
    r = q(s, """SELECT id FROM cenario WHERE tipo = 'projecao'
                 ORDER BY id LIMIT 1""")
    if not r:
        pytest.skip("nenhum cenário de projeção no banco")
    return r[0]["id"]


@pytest.fixture(scope="module")
def emp(s, cenario):
    return q(s, "SELECT empreendimento_id e FROM cenario WHERE id = :c",
             c=cenario)[0]["e"]


# ------------------------------------------------------------- estrutura
def test_todo_setor_aponta_para_uma_linha_do_resultado(s):
    """Um setor cuja `linha_dre` não existe some do estudo sem avisar."""
    rotulos = {r for r, _ in LINHAS_DRE}
    for st in q(s, "SELECT codigo, linha_dre FROM setor_custo"):
        assert st["linha_dre"] in rotulos, (
            f"o setor {st['codigo']} aponta para "
            f"'{st['linha_dre']}', que não é linha do resultado")


def test_todo_setor_composto_tem_verbete(s):
    """Se o sistema pede um número, ele explica qual número é."""
    for st in q(s, "SELECT codigo, nome FROM setor_custo"):
        assert glossario.ajuda(st["codigo"]), (
            f"o setor '{st['nome']}' não tem verbete no glossário")


def test_a_linha_do_resultado_de_cada_setor_tem_ajuda(s):
    for st in q(s, "SELECT codigo, linha_dre FROM setor_custo"):
        assert glossario.ajuda_da_linha(st["linha_dre"]), (
            f"a linha '{st['linha_dre']}' abre sem explicação nenhuma")


def test_setores_compostos_batem_com_a_tabela(s):
    """A lista do código e a do banco precisam ser a mesma."""
    no_banco = {r["codigo"] for r in q(s, "SELECT codigo FROM setor_custo")}
    assert no_banco == set(SETORES_COMPOSTOS)


# --------------------------------------------------------- soma = linha
def _linha_do_resultado(s, cenario_id, rotulo) -> float:
    dre, _, _ = calcular(carregar_entradas(s, cenario_id))
    campo = next(c for r, c in LINHAS_DRE if r == rotulo)
    return float(getattr(dre, campo))


@pytest.fixture
def composicao_limpa(s, cenario):
    """Monta uma composição de teste e devolve o banco ao estado anterior."""
    codigo = "regularizacao_fundiaria"
    antes = q(s, """SELECT descricao, quantidade, unidade, valor_unitario,
                           valor, observacao, ordem
                      FROM composicao_item
                     WHERE cenario_id = :c AND setor = :s ORDER BY ordem""",
              c=cenario, s=codigo)
    yield codigo
    q(s, "DELETE FROM composicao_item WHERE cenario_id = :c AND setor = :s",
      c=cenario, s=codigo)
    for i in antes:
        q(s, """INSERT INTO composicao_item
                  (cenario_id, setor, ordem, descricao, quantidade, unidade,
                   valor_unitario, valor, observacao)
                VALUES (:c, :s, :o, :d, :q, :u, :vu, :v, :ob)""",
          c=cenario, s=codigo, o=i["ordem"], d=i["descricao"],
          q=i["quantidade"], u=i["unidade"], vu=i["valor_unitario"],
          v=i["valor"], ob=i["observacao"])
    s.commit()


def test_a_linha_e_exatamente_a_soma_da_composicao(s, emp, cenario, composicao_limpa):
    codigo = composicao_limpa
    rotulo = q(s, "SELECT linha_dre FROM setor_custo WHERE codigo = :c",
               c=codigo)[0]["linha_dre"]

    itens = [
        {"descricao": "Retificação de área e georreferenciamento",
         "quantidade": None, "unidade": "vb", "valor_unitario": None,
         "valor": 180_000.00, "observacao": "proposta de topografia"},
        {"descricao": "Certidões e baixa de ônus", "quantidade": 12.0,
         "unidade": "un", "valor_unitario": 1_250.00, "valor": 15_000.00,
         "observacao": ""},
    ]
    edicao.gravar_composicao(s, emp_id=emp, cenario_id=cenario,
                             codigo=codigo, itens=itens, autor="teste")
    s.commit()

    soma = sum(i["valor"] for i in itens)
    assert somar_composicoes(s, cenario)[codigo] == pytest.approx(soma, abs=CENT)
    # a linha é negativa: o motor é quem aplica o sinal
    assert _linha_do_resultado(s, cenario, rotulo) == pytest.approx(-soma, abs=CENT)


def test_setor_sem_item_vale_zero(s, emp, cenario, composicao_limpa):
    codigo = composicao_limpa
    rotulo = q(s, "SELECT linha_dre FROM setor_custo WHERE codigo = :c",
               c=codigo)[0]["linha_dre"]
    edicao.gravar_composicao(s, emp_id=emp, cenario_id=cenario,
                             codigo=codigo, itens=[], autor="teste")
    s.commit()
    assert _linha_do_resultado(s, cenario, rotulo) == pytest.approx(0.0, abs=CENT)


def test_a_abertura_da_linha_lista_os_mesmos_itens(s, emp, cenario, composicao_limpa):
    """A tela de procedência não pode contar uma história diferente da conta."""
    codigo = composicao_limpa
    rotulo = q(s, "SELECT linha_dre FROM setor_custo WHERE codigo = :c",
               c=codigo)[0]["linha_dre"]
    itens = [{"descricao": "Usucapião administrativa", "quantidade": None,
              "unidade": None, "valor_unitario": None, "valor": 42_000.00,
              "observacao": ""}]
    edicao.gravar_composicao(s, emp_id=emp, cenario_id=cenario,
                             codigo=codigo, itens=itens, autor="teste")
    s.commit()

    setor, listados = procedencia.composicao_da_linha(s, cenario, rotulo)
    assert setor["codigo"] == codigo
    assert [i["descricao"] for i in listados] == ["Usucapião administrativa"]

    f = procedencia.formula_da_linha(rotulo, *_pecas(s, cenario), listados)
    assert f.resultado == pytest.approx(-42_000.00, abs=CENT)
    assert f.resultado == pytest.approx(_linha_do_resultado(s, cenario, rotulo),
                                        abs=CENT)


def test_linha_que_nao_e_setor_nao_vira_composicao(s, cenario):
    """`None` e lista vazia querem dizer coisas diferentes, e a diferença conta."""
    setor, itens = procedencia.composicao_da_linha(s, cenario, "VGV")
    assert setor is None and itens is None


def _pecas(s, cenario_id):
    from app.motor.engine import calcular_vgv
    e = carregar_entradas(s, cenario_id)
    dre, _, _ = calcular(e)
    return dre, calcular_vgv(e.unidades, e.premissas, e.tabela), e.obra, e.premissas


def test_nao_sobrou_premissa_escalar_de_setor(s):
    """
    A migration 011 apagou o número solto. Se um voltar, a linha passa a ter
    duas fontes possíveis — e esvaziar a composição faria o valor antigo
    ressuscitar em vez de zerar.
    """
    sobrou = q(s, """SELECT p.chave, count(*) n FROM premissa p
                      JOIN setor_custo s ON s.codigo = p.chave
                     GROUP BY p.chave""")
    assert not sobrou, f"premissa escalar sobrevivendo: {sobrou}"


# =====================================================================
# o cronograma mandando no desembolso
# =====================================================================
@pytest.fixture
def cronograma_de_teste(s, emp, cenario):
    """
    Monta um cronograma mínimo para a regularização fundiária e desmonta depois.

    Dois marcos, em meses diferentes e distantes da obra: é justamente o caso em
    que a regra antiga (diluir pela curva física) erra a data, e portanto o caso
    que prova que agora o calendário manda.
    """
    import datetime as dt
    base = q(s, "SELECT mes_base FROM cenario WHERE id = :c", c=cenario)[0]["mes_base"]
    m1 = dt.date(base.year + 1, 3, 31)
    m2 = dt.date(base.year + 3, 9, 30)

    # O empreendimento pode já ter cronograma de verdade — e tem, em produção.
    # Para o teste medir só os dois marcos que ele cria, as outras áreas que
    # alimentam este setor ficam temporariamente sem setor, e voltam ao fim.
    setor = "regularizacao_fundiaria"
    outras = [l["codigo"] for l in
              q(s, "SELECT codigo FROM area_pdp WHERE setor = :s", s=setor)]
    q(s, "UPDATE area_pdp SET setor = NULL WHERE setor = :s", s=setor)
    q(s, """INSERT INTO area_pdp (codigo, nome, setor, ordem)
            VALUES ('TST', 'Área de teste', :s, 999)
            ON CONFLICT (codigo) DO UPDATE SET setor = EXCLUDED.setor""", s=setor)

    ids = []
    for pdp_id, nome, fim in (("T001", "Matrículas retificadas", m1),
                              ("T002", "Matrículas unificadas", m2)):
        ids.append(q(s, """INSERT INTO marco (empreendimento_id, pdp_id, nome,
                                area_codigo, inicio, fim, duracao, progresso, critico)
                           VALUES (:e, :p, :n, 'TST', :i, :f, 30, 0, false)
                           RETURNING id""",
                     e=emp, p=pdp_id, n=nome, i=fim, f=fim)[0]["id"])
    s.commit()
    yield {"ids": ids, "meses": [m1, m2]}
    q(s, "DELETE FROM marco WHERE empreendimento_id = :e AND pdp_id LIKE 'T00%'", e=emp)
    q(s, "DELETE FROM area_pdp WHERE codigo = 'TST'")
    for cod in outras:
        q(s, "UPDATE area_pdp SET setor = :s WHERE codigo = :c", s=setor, c=cod)
    s.commit()


def _linha_do_fluxo(s, cenario_id, codigo):
    from app.repositorio import carregar_entradas
    _, fluxo, _ = calcular(carregar_entradas(s, cenario_id))
    return next(l for l in fluxo.linhas if l.codigo == codigo)


def test_o_desembolso_cai_nos_meses_dos_marcos(s, emp, cenario,
                                               composicao_limpa, cronograma_de_teste):
    """Sem marco, o custo seguia a curva da obra. Com marco, segue o calendário."""
    codigo = composicao_limpa
    edicao.gravar_composicao(
        s, emp_id=emp, cenario_id=cenario, codigo=codigo, autor="teste",
        itens=[{"descricao": "Verba de cartório", "quantidade": None,
                "unidade": None, "valor_unitario": None, "valor": 200_000.0,
                "observacao": ""}])
    s.commit()

    linha = _linha_do_fluxo(s, cenario, "REG_FUND")
    meses_com_valor = {m for m, v in linha.valores.items() if abs(v) > 0.005}
    assert meses_com_valor == set(cronograma_de_teste["meses"]), (
        "o desembolso tinha de cair exatamente nos dois meses de marco")
    for m in cronograma_de_teste["meses"]:
        assert linha.valores[m] == pytest.approx(-100_000.0, abs=CENT)


def test_item_amarrado_a_um_marco_sai_na_data_dele(s, emp, cenario,
                                                   composicao_limpa, cronograma_de_teste):
    """
    Um item com marco não entra na média: sai inteiro no mês daquele marco.
    O resto do setor continua repartido pelos meses da área.
    """
    codigo = composicao_limpa
    primeiro, segundo = cronograma_de_teste["meses"]
    edicao.gravar_composicao(
        s, emp_id=emp, cenario_id=cenario, codigo=codigo, autor="teste",
        itens=[{"descricao": "Taxa paga no protocolo", "quantidade": None,
                "unidade": None, "valor_unitario": None, "valor": 60_000.0,
                "observacao": "", "marco_id": cronograma_de_teste["ids"][0]},
               {"descricao": "Verba de cartório", "quantidade": None,
                "unidade": None, "valor_unitario": None, "valor": 40_000.0,
                "observacao": ""}])
    s.commit()

    linha = _linha_do_fluxo(s, cenario, "REG_FUND")
    # 60.000 no marco escolhido + metade dos 40.000 soltos
    assert linha.valores[primeiro] == pytest.approx(-80_000.0, abs=CENT)
    assert linha.valores[segundo] == pytest.approx(-20_000.0, abs=CENT)
    assert sum(linha.valores.values()) == pytest.approx(-100_000.0, abs=CENT)


def test_o_cronograma_muda_o_caixa_e_nao_o_resultado(s, emp, cenario,
                                                     composicao_limpa, cronograma_de_teste):
    """
    A promessa do módulo em uma linha: quando o dinheiro sai é caixa; quanto
    custa é resultado. Mexer no cronograma não pode mexer no lucro.
    """
    codigo = composicao_limpa
    itens = [{"descricao": "Verba de cartório", "quantidade": None, "unidade": None,
              "valor_unitario": None, "valor": 200_000.0, "observacao": ""}]
    edicao.gravar_composicao(s, emp_id=emp, cenario_id=cenario, codigo=codigo,
                             itens=itens, autor="teste")
    s.commit()
    from app.repositorio import carregar_entradas
    com_marcos, _, _ = calcular(carregar_entradas(s, cenario))

    q(s, "DELETE FROM marco WHERE empreendimento_id = :e AND pdp_id LIKE 'T00%'", e=emp)
    s.commit()
    sem_marcos, _, _ = calcular(carregar_entradas(s, cenario))

    assert com_marcos.lucro == pytest.approx(sem_marcos.lucro, abs=CENT)
    assert com_marcos.regularizacao_fundiaria == pytest.approx(
        sem_marcos.regularizacao_fundiaria, abs=CENT)


def test_area_sem_setor_nao_puxa_custo_nenhum(s):
    """
    Engenharia e Orçamento têm marco e não têm setor: o trabalho delas já está
    orçado em outro lugar. Se um dia ganharem setor por engano, o custo daquele
    setor passaria a ser distribuído pela obra inteira sem ninguém notar.
    """
    sem_setor = {l["nome"] for l in
                 q(s, "SELECT nome FROM area_pdp WHERE setor IS NULL")}
    assert {"Engenharia", "Orçamento", "Controladoria", "Novos Negócios"} <= sem_setor


# =====================================================================
# simulação de atraso
# =====================================================================
def test_atraso_anda_pela_rede():
    """A → B → C. Atrasar A move os três; C não anda mais do que o pior caminho."""
    from app.cronograma import propagar
    marcos = [{"pdp_id": "A"}, {"pdp_id": "B"}, {"pdp_id": "C"}, {"pdp_id": "D"}]
    deps = {"B": [("A", "TI", 0)], "C": [("B", "TI", 0)], "D": []}
    assert propagar(marcos, deps, {"A": 30}) == {"A": 30, "B": 30, "C": 30}


def test_sucessor_herda_o_pior_predecessor():
    """Quem espera dois, espera o mais atrasado — não a soma dos dois."""
    from app.cronograma import propagar
    marcos = [{"pdp_id": x} for x in "ABC"]
    deps = {"C": [("A", "TI", 0), ("B", "TI", 0)]}
    assert propagar(marcos, deps, {"A": 10, "B": 45}) == {"A": 10, "B": 45, "C": 45}


def test_adiantar_nao_puxa_ninguem_para_tras():
    """
    Um marco que termina antes não obriga o seguinte a começar antes: quem vem
    depois tem os próprios motivos para a data que tem. A propagação é sempre
    para a frente, de propósito.
    """
    from app.cronograma import propagar
    marcos = [{"pdp_id": "A"}, {"pdp_id": "B"}]
    assert propagar(marcos, {"B": [("A", "TI", 0)]}, {"A": -20}) == {}


def test_ciclo_no_cadastro_nao_trava_a_simulacao(caplog):
    """
    Cronograma com ciclo existe — é erro de cadastro, e acontece. O que não pode
    é a tela inteira parar por causa dele.
    """
    from app.cronograma import propagar
    import datetime as dt
    marcos = [{"pdp_id": "A", "inicio": dt.date(2027, 1, 1)},
              {"pdp_id": "B", "inicio": dt.date(2027, 2, 1)},
              {"pdp_id": "Z", "inicio": dt.date(2027, 3, 1)}]
    deps = {"A": [("B", "TI", 0)], "B": [("A", "TI", 0)], "Z": [("A", "TI", 0)]}
    movidos = propagar(marcos, deps, {"A": 15})
    assert movidos["A"] == 15 and movidos["Z"] == 15


def test_cenario_de_simulacao_muda_o_caixa_e_nao_o_lucro(s, emp, cenario,
                                                          composicao_limpa,
                                                          cronograma_de_teste):
    """
    A mesma promessa do módulo, agora atravessando um cenário inteiro: atrasar
    marco move dinheiro no tempo, não cria nem destrói dinheiro.
    """
    from app.cronograma import salvar_como_cenario
    from app.repositorio import carregar_entradas
    codigo = composicao_limpa
    edicao.gravar_composicao(
        s, emp_id=emp, cenario_id=cenario, codigo=codigo, autor="teste",
        itens=[{"descricao": "Verba de cartório", "quantidade": None,
                "unidade": None, "valor_unitario": None, "valor": 200_000.0,
                "observacao": ""}])
    s.commit()
    antes, _, _ = calcular(carregar_entradas(s, cenario))

    novo = salvar_como_cenario(s, emp, cenario, "atraso de teste",
                               {"T001": 90}, "teste")
    s.commit()
    try:
        depois, fluxo, _ = calcular(carregar_entradas(s, novo))
        assert depois.lucro == pytest.approx(antes.lucro, abs=CENT)
        linha = next(l for l in fluxo.linhas if l.codigo == "REG_FUND")
        assert sum(linha.valores.values()) == pytest.approx(-200_000.0, abs=1.0)
    finally:
        q(s, "DELETE FROM cenario WHERE id = :c", c=novo)
        s.commit()


def test_a_rodada_enxerga_o_cronograma(s, emp, cenario, composicao_limpa,
                                       cronograma_de_teste):
    """
    O hash da rodada precisa incluir o cronograma.

    Sem isso acontecem duas coisas ruins e silenciosas: a rodada estoura ao
    serializar (data como chave de dicionário), ou — pior — dois cenários com
    cronogramas diferentes acham que são a mesma entrada e compartilham o
    resultado. Este teste roda o caminho inteiro, que é onde isso aparece.
    """
    from app.servico import rodar_e_persistir
    from app.cronograma import salvar_como_cenario
    codigo = composicao_limpa
    edicao.gravar_composicao(
        s, emp_id=emp, cenario_id=cenario, codigo=codigo, autor="teste",
        itens=[{"descricao": "Verba", "quantidade": None, "unidade": None,
                "valor_unitario": None, "valor": 120_000.0, "observacao": ""}])
    s.commit()

    # dois cenários derivados, com atrasos diferentes: nenhum toca o cenário de
    # origem, que é de onde a tela de viabilidade lê
    a = salvar_como_cenario(s, emp, cenario, "atraso curto", {"T001": 30}, "teste")
    b = salvar_como_cenario(s, emp, cenario, "atraso longo", {"T001": 400}, "teste")
    s.commit()
    try:
        r1 = rodar_e_persistir(s, a, executada_por="teste", forcar=True)
        r2 = rodar_e_persistir(s, b, executada_por="teste", forcar=True)
        s.commit()
        assert r1 != r2, ("cronogramas diferentes viraram a mesma rodada — o "
                          "hash das entradas não está enxergando o calendário")
        ind = {l["rodada_id"]: float(l["lucro"]) for l in
               q(s, "SELECT rodada_id, lucro FROM indicador WHERE rodada_id = ANY(:r)",
                 r=[r1, r2])}
        assert len(ind) == 2, "os dois cenários precisam ter indicador gravado"
        assert ind[r1] == pytest.approx(ind[r2], abs=1.0), (
            "atrasar marco move caixa, não muda lucro")
    finally:
        q(s, "DELETE FROM cenario WHERE id = ANY(:c)", c=[a, b])
        s.commit()


# =====================================================================
# folga
# =====================================================================
@pytest.mark.parametrize("de,ate,esperado", [
    ("2027-06-03", "2027-06-03", 0),
    ("2027-06-03", "2027-06-04", 1),
    ("2027-06-04", "2027-06-07", 1),      # sexta → segunda: um dia útil
    ("2027-06-03", "2027-06-10", 5),
    ("2027-06-10", "2027-06-03", -5),
])
def test_dias_uteis_pula_o_fim_de_semana(de, ate, esperado):
    import datetime as dt
    from app.cronograma import dias_uteis
    assert dias_uteis(dt.date.fromisoformat(de), dt.date.fromisoformat(ate)) == esperado


def test_somar_uteis_e_o_inverso_de_dias_uteis():
    import datetime as dt
    from app.cronograma import dias_uteis, somar_uteis
    base = dt.date(2027, 6, 3)
    for n in (0, 1, 5, 22, -7):
        assert dias_uteis(base, somar_uteis(base, n)) == n


def test_folga_zero_no_caminho_unico():
    """Numa corrente sem alternativa, todo mundo é crítico."""
    import datetime as dt
    from app.cronograma import folgas
    d = dt.date
    marcos = [{"pdp_id": "A", "inicio": d(2027, 1, 4), "fim": d(2027, 1, 8)},
              {"pdp_id": "B", "inicio": d(2027, 1, 11), "fim": d(2027, 1, 15)}]
    f = folgas(marcos, {"B": [("A", "TI", 0)]})
    assert f["A"]["total"] == 0 and f["A"]["livre"] == 0
    assert f["B"]["total"] == 0


def test_o_caminho_curto_tem_folga_e_o_longo_nao():
    """
    A destrava B e C; os dois desembocam em D. O ramo curto sobra tempo — e é
    exatamente isso que a folga precisa mostrar para alguém saber onde apertar.
    """
    import datetime as dt
    from app.cronograma import folgas
    d = dt.date
    marcos = [
        {"pdp_id": "A", "inicio": d(2027, 1, 4), "fim": d(2027, 1, 8)},
        {"pdp_id": "curto", "inicio": d(2027, 1, 11), "fim": d(2027, 1, 15)},
        {"pdp_id": "longo", "inicio": d(2027, 1, 11), "fim": d(2027, 2, 12)},
        {"pdp_id": "D", "inicio": d(2027, 2, 15), "fim": d(2027, 2, 19)},
    ]
    deps = {"curto": [("A", "TI", 0)], "longo": [("A", "TI", 0)],
            "D": [("curto", "TI", 0), ("longo", "TI", 0)]}
    f = folgas(marcos, deps)
    assert f["longo"]["total"] == 0, "o ramo longo manda no prazo"
    assert f["curto"]["total"] == 20, "o ramo curto sobra quatro semanas úteis"
    assert f["curto"]["livre"] == 20
    assert f["A"]["total"] == 0 and f["D"]["total"] == 0


def test_folga_livre_e_menor_que_a_total_quando_o_sucessor_e_quem_sobra():
    """
    A → B, e B tem folga própria. A pode atrasar até encostar em B sem
    consequência nenhuma (folga livre), e além disso só às custas da folga de B
    (folga total). Confundir as duas é prometer prazo que não existe.
    """
    import datetime as dt
    from app.cronograma import folgas
    d = dt.date
    marcos = [{"pdp_id": "A", "inicio": d(2027, 1, 4), "fim": d(2027, 1, 8)},
              {"pdp_id": "B", "inicio": d(2027, 1, 25), "fim": d(2027, 1, 29)},
              {"pdp_id": "fim", "inicio": d(2027, 3, 1), "fim": d(2027, 3, 5)}]
    deps = {"B": [("A", "TI", 0)], "fim": [("B", "TI", 0)]}
    f = folgas(marcos, deps)
    assert f["A"]["livre"] == 10, "dez dias úteis até encostar em B"
    assert f["A"]["total"] > f["A"]["livre"], "além disso, come a folga de B"


def test_a_folga_concorda_com_o_caminho_critico_do_pdp(s, emp):
    """
    A prova de que a conta está certa: o caminho crítico que a folga aponta tem
    de ser o mesmo que o PDP marcou. São dois cálculos independentes — um feito
    lá, outro aqui — e eles precisam chegar no mesmo conjunto de marcos.

    Diferença aqui costuma ser feriado, que esta conta não conhece. Diferença
    grande é erro de leitura dos vínculos.
    """
    from app.cronograma import dados_do_gantt
    cen = q(s, """SELECT id FROM cenario WHERE empreendimento_id = :e
                   AND tipo = 'projecao' ORDER BY id LIMIT 1""", e=emp)
    if not cen:
        pytest.skip("empreendimento sem cenário de projeção")
    d = dados_do_gantt(s, emp, cen[0]["id"])
    if not d["marcos"] or not d["criticos_pdp"]:
        pytest.skip("empreendimento sem cronograma sincronizado")
    assert not d["divergentes"], (
        f"o PDP marca {d['criticos_pdp']} marcos como críticos e a folga aponta "
        f"{d['criticos_folga']}; divergem: {d['divergentes'][:10]}")
