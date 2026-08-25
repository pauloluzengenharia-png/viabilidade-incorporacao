"""
Conferência do sistema contra a planilha de origem.

Roda sobre o banco já migrado (`python3 migrar_kiev.py dados/kiev.xlsx`).
Cada teste é uma linha que a planilha calculava; se o sistema divergir, o
teste quebra e diz em qual célula estava o número original.

    DATABASE_URL=... python3 -m pytest tests -q
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"),
                                reason="precisa de DATABASE_URL apontando para o banco migrado")

from app.db import Sessao, q                      # noqa: E402
from app.repositorio import carregar_entradas     # noqa: E402
from app.servico import calcular, visao_viabilidade  # noqa: E402

CENT = 0.01
REAL = 1.0
# a planilha usa 1,5001661% em "outras despesas administrativas" — um dígito
# espúrio de uma divisão feita uma vez. O sistema usa 1,5% redondo.
TOLERANCIA_DIGITO_ESPURIO = 300.0


@pytest.fixture(scope="module")
def s():
    ses = Sessao()
    yield ses
    ses.close()


@pytest.fixture(scope="module")
def emp(s):
    r = q(s, "SELECT id FROM empreendimento WHERE sienge_enterprise_id = 26003")
    assert r, "rode `python3 migrar_kiev.py dados/kiev.xlsx` primeiro"
    return r[0]["id"]


@pytest.fixture(scope="module")
def cenario_realista(s, emp):
    return q(s, """SELECT id FROM cenario
                    WHERE empreendimento_id = :e AND nome = 'realista'
                      AND tipo = 'projecao'""", e=emp)[0]["id"]


@pytest.fixture(scope="module")
def resultado(s, cenario_realista):
    return calcular(carregar_entradas(s, cenario_realista))


# ---------------------------------------------------------------- cadastro
def test_area_privativa(s, emp):
    """SIMULAÇÕES L58 = Σ Comercial!C6:C205"""
    a = q(s, """SELECT sum(area_privativa) a FROM unidade
                 WHERE empreendimento_id = :e AND considerar_na_viabilidade""",
          e=emp)[0]["a"]
    assert float(a) == pytest.approx(10_731.06, abs=CENT)


def test_composicao_do_estoque(s, emp):
    """SIMULAÇÕES K6/K9/K11/K13: 39 vendidas, 5 permutas, 152 estoque, 4 investidor."""
    linhas = q(s, """SELECT situacao, tipo_venda, count(*) n FROM unidade
                      WHERE empreendimento_id = :e AND considerar_na_viabilidade
                      GROUP BY 1, 2""", e=emp)
    contagem = {(l["situacao"], l["tipo_venda"]): l["n"] for l in linhas}
    assert contagem[("Vendida", "Normal")] == 39
    assert contagem[("Permuta", "Leal")] == 5
    assert contagem[("Disponível", "Normal")] == 152
    assert contagem[("Disponível", "Investidor")] == 4


def test_unidades_fora_do_vgv_estao_marcadas(s, emp):
    """As duas salas da garagem 02 existem no Sienge e ficam fora do estudo."""
    fora = q(s, """SELECT nome, motivo_exclusao FROM unidade
                    WHERE empreendimento_id = :e AND NOT considerar_na_viabilidade""",
             e=emp)
    assert {f["nome"] for f in fora} == {"SALA 1 - G 02", "SALA 2 - G 02"}
    assert all(f["motivo_exclusao"] for f in fora), "exclusão sem motivo registrado"


def test_carteira_bate_com_a_planilha(s, emp):
    """Comercial!N207 (recebido) e CH207 (a receber)."""
    rec = q(s, """SELECT sum(valor_liquido) v FROM parcela_recebida p
                    JOIN unidade u ON u.id = p.unidade_id
                   WHERE u.empreendimento_id = :e""", e=emp)[0]["v"]
    car = q(s, """SELECT sum(valor) v FROM parcela_receber p
                    JOIN unidade u ON u.id = p.unidade_id
                   WHERE u.empreendimento_id = :e""", e=emp)[0]["v"]
    assert float(rec) == pytest.approx(1_618_169.93, abs=CENT)
    assert float(car) == pytest.approx(23_871_386.58, abs=CENT)


def test_incorrido_importado(s, emp):
    """Fin_Obra: 768 lançamentos, todos com conta."""
    n = q(s, """SELECT count(*) n, count(conta_id) c FROM movimento_realizado
                 WHERE empreendimento_id = :e""", e=emp)[0]
    assert n["n"] == 768
    assert n["c"] == 768, "lançamento sem conta some do realizado"


# ---------------------------------------------------------------- DRE
@pytest.mark.parametrize("campo,alvo,celula,tol", [
    ("vgv",                 197_521_166.49, "SIMULAÇÕES M17", CENT),
    ("comissao",             -9_302_095.40, "M19",            CENT),
    ("receita_spe",         188_219_071.09, "M23",            CENT),
    ("ret",                  -8_469_858.20, "M26",            CENT),
    ("despesas_comerciais",     -30_750.00, "M28",            CENT),
    ("receita_liquida",     179_718_462.89, "M30",            CENT),
    ("terreno_permuta",     -27_364_730.00, "M33",            CENT),
    ("terreno_pagamento",    -3_450_000.00, "M34",            CENT),
    ("terreno_registro",        -86_250.00, "M35",            CENT),
    ("obra_custo_raso",    -104_049_038.53, "M36",            CENT),
    ("taxa_adm_obra",       -10_404_903.85, "M37",            CENT),
    ("taxa_viabilizacao",    -9_410_953.55, "M38",            CENT),
    ("decoracao",            -1_545_045.14, "M40",            CENT),
    ("projetos_e_outros",    -2_899_156.99, "M41",            CENT),
    ("marketing_propaganda", -1_545_045.14, "M43",            CENT),
    ("gastos",             -162_315_913.71, "M32", TOLERANCIA_DIGITO_ESPURIO),
    ("lucro",                17_402_549.18, "M54", TOLERANCIA_DIGITO_ESPURIO),
])
def test_linha_do_dre(resultado, campo, alvo, celula, tol):
    dre, _, _ = resultado
    assert getattr(dre, campo) == pytest.approx(alvo, abs=tol), f"planilha: {celula}"


def test_margem(resultado):
    dre, _, _ = resultado
    assert dre.margem == pytest.approx(0.096832, abs=1e-5), "planilha: SIMULAÇÕES N54"


# ---------------------------------------------------------------- indicadores
def test_custo_por_m2(resultado):
    _, _, ind = resultado
    assert ind.custo_m2_privativa == pytest.approx(9_696.0634, abs=0.001), "F36"


def test_eficiencia(resultado):
    _, _, ind = resultado
    assert ind.eficiencia == pytest.approx(0.4684117, abs=1e-6), "L59"


def test_tir_nao_finge_existir(resultado):
    """
    A planilha reporta 163,7% (L65) a partir de uma série ajustada à mão.
    Sem o ajuste o fluxo troca de sinal várias vezes: ou a TIR existe de
    verdade, ou o sistema devolve None e usa a MTIR.
    """
    _, _, ind = resultado
    assert ind.mtir_anual is not None, "sem MTIR não há indicador de retorno utilizável"
    if ind.tir_anual is not None:
        assert -1 < ind.tir_anual < 10


# ---------------------------------------------------------------- fluxo
def test_obra_desembolsa_o_custo_inteiro(resultado):
    """
    A grande correção em relação à planilha: lá a linha 37 da projeção é um
    conjunto de valores colados, desligado do cronograma. Aqui o desembolso
    é a curva aplicada ao custo, e portanto fecha.
    """
    _, fluxo, _ = resultado
    total = sum(fluxo.linha("OBRA").valores.values())
    # tolerância de R$ 10 sobre R$ 104 M: os pesos da EAP e as curvas são
    # gravados com 8 casas, o que deixa centavos de resíduo na recomposição
    assert total == pytest.approx(-104_049_038.53, abs=10.0)


def test_taxa_de_obra_seque_a_obra(resultado):
    _, fluxo, _ = resultado
    obra = sum(fluxo.linha("OBRA").valores.values())
    taxa = sum(fluxo.linha("TX_OBRA").valores.values())
    assert taxa == pytest.approx(obra * 0.10, abs=REAL)


def test_conservacao_da_carteira(s, cenario_realista):
    """
    Nenhum real da carteira projetada pode sumir: a soma do fluxo de estoque
    mais o resíduo pós-chaves tem de bater com o VGV das coortes.
    """
    from app.motor.engine import eixo_meses, gerar_coortes, recebiveis_estoque
    from app.servico import valor_unidade_padrao
    e = carregar_entradas(s, cenario_realista)
    vu = valor_unidade_padrao(e)
    coortes = [c for c in gerar_coortes(e.plano, e.premissas, vu)
               if c.tipo.value != "Investidor"]
    total = sum(c.valor_unitario * c.quantidade for c in coortes)
    meses = eixo_meses(e.premissas.mes_base, 200)
    fluxo = recebiveis_estoque(coortes, e.tabela, e.premissas, meses,
                               mes_chaves=dt.date(2031, 3, 31))
    assert sum(fluxo.values()) == pytest.approx(total, abs=REAL)


def test_exposicao_de_caixa_e_negativa_e_datada(resultado):
    _, _, ind = resultado
    assert ind.exposicao_maxima < 0, "projeto de incorporação sem exposição é suspeito"
    assert ind.mes_exposicao is not None
    assert ind.aporte_necessario == pytest.approx(abs(ind.exposicao_maxima), abs=CENT)


# ---------------------------------------------------------------- cenários
@pytest.mark.parametrize("nome,alvo,celula", [
    ("otimista",   15_746_031.95, "SIMULAÇÕES G54"),
    ("realista",   17_402_549.18, "M54"),
    ("pessimista", 14_836_796.85, "S54"),
])
def test_lucro_dos_tres_cenarios(s, emp, nome, alvo, celula):
    cid = q(s, """SELECT id FROM cenario WHERE empreendimento_id = :e
                   AND nome = :n AND tipo = 'projecao'""", e=emp, n=nome)[0]["id"]
    dre, _, _ = calcular(carregar_entradas(s, cid))
    assert dre.lucro == pytest.approx(alvo, abs=TOLERANCIA_DIGITO_ESPURIO), celula


# ---------------------------------------------------------------- viabilidade
def test_visao_viabilidade_nao_tem_ref(s, emp):
    """
    A aba VIABILIDADE da planilha tem 18 células com #REF!. A visão do
    sistema não pode ter nenhum buraco: toda linha existe e é numérica.
    """
    linhas = visao_viabilidade(s, emp)
    assert linhas, "a visão veio vazia — faltou rodar os cenários"
    for l in linhas:
        for campo in ("orcado", "atualizado", "realizado", "a_realizar"):
            assert isinstance(l[campo], float), f"{l['linha']}.{campo} não é número"


def test_realizado_respeita_a_data_de_corte(s, emp):
    """
    O que a planilha faz movendo a fronteira entre as colunas J:AC e AF:DQ,
    aqui é um parâmetro: mudar a data de corte muda o realizado.
    """
    cheio = visao_viabilidade(s, emp, ate=dt.date(2026, 7, 31))
    curto = visao_viabilidade(s, emp, ate=dt.date(2021, 12, 31))
    soma = lambda v: sum(abs(l["realizado"]) for l in v)
    assert soma(curto) < soma(cheio)


# ---------------------------------------------------------------- realizado
# A coluna AD do fluxo mensal é o "realizado" da planilha. Onde o cache do
# Excel bate com a própria fonte, o sistema tem de bater também.
REALIZADO_AD = {
    "RECEITA C/ VENDAS SPE":                 1_737_912.44,
    "(-) Impostos":                                 -22.30,
    "(-) Despesas comerciais":                  -31_596.00,
    "(-) Terreno - Outros":                     -25_000.00,
    "(-) Obra - Custo Raso":                    -39_855.71,
    "(-) Incorporação - Outros":             -1_267_253.07,
    "(-) Marketing - Stand":                     -7_100.00,
    "(-) Marketing - Propaganda":              -150_439.25,
    "(-) Outras despesas administrativas":      -37_569.18,
    "(+) Outras receitas administrativas":       54_572.53,
}


@pytest.mark.parametrize("linha,alvo", sorted(REALIZADO_AD.items()))
def test_realizado_bate_com_a_planilha(s, emp, linha, alvo):
    v = {l["linha"]: l["realizado"] for l in visao_viabilidade(s, emp)}
    assert v[linha] == pytest.approx(alvo, abs=REAL), "coluna AD do fluxo mensal"


def test_terreno_realizado_corrige_cache_velho_da_planilha(s, emp):
    """
    A planilha mostra R$ 3.213.762,81 de terreno pago (VM AD35), mas a soma
    dos próprios lançamentos do Sienge dá R$ 1.863.762,81: a célula J35 tem
    um SUMIFS cujo cache ficou R$ 1,35 M acima da fonte e nunca foi
    recalculado. O sistema lê a fonte, então diverge — de propósito.
    """
    v = {l["linha"]: l["realizado"] for l in visao_viabilidade(s, emp)}
    assert v["(-) Terreno - Pagamento"] == pytest.approx(-1_863_762.81, abs=REAL)


def test_comissao_realizada_respeita_a_data_de_corte(s, emp):
    """
    A planilha soma a comissão de todas as unidades vendidas sem olhar a data
    do contrato (VM AD13). Um contrato de 03/08/2026 entra num realizado
    fechado em 31/07/2026 — R$ 47.190,48 que ainda não aconteceram.
    """
    ate_julho = {l["linha"]: l["realizado"]
                 for l in visao_viabilidade(s, emp, ate=dt.date(2026, 7, 31))}
    ate_agosto = {l["linha"]: l["realizado"]
                  for l in visao_viabilidade(s, emp, ate=dt.date(2026, 8, 31))}
    assert ate_julho["(-) Comissão s/ vendas"] == pytest.approx(-1_560_432.50, abs=REAL)
    assert ate_agosto["(-) Comissão s/ vendas"] == pytest.approx(-1_607_622.98, abs=REAL)


# ---------------------------------------------------------------- correção
def _com_correcao(s, cenario_id, projetado_aa=0.065, obra=True):
    q(s, """UPDATE cenario SET indice_ate_chaves = 'INCC-DI',
                               indice_apos_chaves = 'IGP-M' WHERE id = :c""", c=cenario_id)
    for chave, valor in [("indice_projetado_aa", projetado_aa),
                         ("corrigir_custo_obra", 1.0 if obra else 0.0)]:
        q(s, """INSERT INTO premissa (cenario_id, chave, valor, unidade, origem)
                VALUES (:c, :k, :v, 'percentual', 'teste')
                ON CONFLICT (cenario_id, chave) DO UPDATE SET valor = EXCLUDED.valor""",
          c=cenario_id, k=chave, v=valor)
    s.commit()
    return calcular(carregar_entradas(s, cenario_id))


def _sem_correcao(s, cenario_id):
    q(s, """UPDATE cenario SET indice_ate_chaves = NULL,
                               indice_apos_chaves = NULL WHERE id = :c""", c=cenario_id)
    q(s, """DELETE FROM premissa WHERE cenario_id = :c
             AND chave IN ('indice_projetado_aa', 'corrigir_custo_obra')""", c=cenario_id)
    s.commit()


def test_sem_indice_o_resultado_e_o_nominal_da_planilha(s, cenario_realista):
    """A correção é opcional: desligada, o sistema projeta em valores nominais."""
    _sem_correcao(s, cenario_realista)
    _, fluxo, _ = calcular(carregar_entradas(s, cenario_realista))
    assert all(l.codigo != "CORRECAO" for l in fluxo.linhas)


def test_serie_historica_do_incc_tem_precedencia(s, cenario_realista):
    """
    Onde há INCC publicado, é ele que vale; a taxa projetada só preenche os
    meses futuros. Jul/2026 saiu 0,61% — não 6,5% a.a. dividido por 12.
    """
    from app.repositorio import carregar_entradas as ce
    _com_correcao(s, cenario_realista)
    p = ce(s, cenario_realista).premissas
    assert p.variacao_do_mes("INCC-DI", dt.date(2026, 7, 31)) == pytest.approx(0.0061)
    assert p.variacao_do_mes("INCC-DI", dt.date(2029, 5, 31)) == pytest.approx(
        p.indice_projetado_mensal)
    _sem_correcao(s, cenario_realista)


def test_correcao_entra_em_linha_propria(s, cenario_realista):
    """
    A correção não infla "venda de imóveis": fica em linha separada, para dar
    para ver quanto do caixa vem de preço e quanto vem de índice.
    """
    _, fluxo, _ = _com_correcao(s, cenario_realista)
    correcao = sum(fluxo.linha("CORRECAO").valores.values())
    assert correcao > 0
    assert sum(fluxo.linha("ESTOQUE").valores.values()) > 0, "a base não pode sumir"
    _sem_correcao(s, cenario_realista)


def test_indice_corrige_os_dois_lados(s, cenario_realista):
    """
    Corrigir só a carteira inventa lucro: o INCC que reajusta a parcela do
    cliente é o mesmo que encarece o concreto. Com os dois lados ligados, a
    obra também tem sua linha de correção, negativa.
    """
    _, fluxo, _ = _com_correcao(s, cenario_realista, obra=True)
    assert sum(fluxo.linha("CORRECAO_OBRA").valores.values()) < 0
    _sem_correcao(s, cenario_realista)


def test_projecao_nominal_subestima_a_exposicao(s, cenario_realista):
    """
    O achado que justifica a funcionalidade: com INCC nos dois lados a
    exposição de caixa PIORA, porque o custo infla durante a obra e a
    correção da carteira só entra depois, diluída em 60 parcelas e nas
    chaves. Projetar em valores nominais subestima a necessidade de aporte.
    """
    _sem_correcao(s, cenario_realista)
    _, _, nominal = calcular(carregar_entradas(s, cenario_realista))
    _, _, corrigido = _com_correcao(s, cenario_realista, obra=True)
    assert corrigido.exposicao_maxima < nominal.exposicao_maxima
    _sem_correcao(s, cenario_realista)
