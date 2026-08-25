"""
Conferência do sistema contra a planilha de origem.

Roda sobre o banco já migrado (`python3 migrar_kiev.py dados/kiev.xlsx`).
Cada teste é uma linha que a planilha calculava; se o sistema divergir, o
teste quebra e diz em qual célula estava o número original.

    DATABASE_URL=... python3 -m pytest tests -q

Os valores esperados ficam em `dados/kiev_esperado.json`, fora do controle de
versão: o repositório é código, e a viabilidade de uma SPE é informação da
empresa. Sem esse arquivo os testes são pulados, não quebrados.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

import pytest

ESPERADO_ARQ = (pathlib.Path(__file__).resolve().parent.parent
                / "dados" / "kiev_esperado.json")

pytestmark = [
    pytest.mark.skipif(not os.getenv("DATABASE_URL"),
                       reason="precisa de DATABASE_URL apontando para o banco migrado"),
    pytest.mark.skipif(not ESPERADO_ARQ.exists(),
                       reason=f"sem {ESPERADO_ARQ.name}: os valores da SPE não são versionados"),
]

E = json.loads(ESPERADO_ARQ.read_text(encoding="utf-8")) if ESPERADO_ARQ.exists() else {}

from app.db import Sessao, q                          # noqa: E402
from app.repositorio import carregar_entradas         # noqa: E402
from app.servico import calcular, visao_viabilidade   # noqa: E402

CENT = 0.01
REAL = 1.0
# a planilha usa 1,5001661% em "outras despesas administrativas" — um dígito
# espúrio de uma divisão feita uma vez. O sistema usa 1,5% redondo.
TOLERANCIA_DIGITO_ESPURIO = 300.0
D = lambda s: dt.date.fromisoformat(s)


@pytest.fixture(scope="module")
def s():
    ses = Sessao()
    yield ses
    ses.close()


@pytest.fixture(scope="module")
def emp(s):
    r = q(s, "SELECT id FROM empreendimento WHERE sienge_enterprise_id = :i",
          i=E["enterprise_id"])
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
    assert float(a) == pytest.approx(E["area_privativa"], abs=CENT)


def test_composicao_do_estoque(s, emp):
    """SIMULAÇÕES K6/K9/K11/K13: vendidas, permutas, estoque e investidor."""
    linhas = q(s, """SELECT situacao, tipo_venda, count(*) n FROM unidade
                      WHERE empreendimento_id = :e AND considerar_na_viabilidade
                      GROUP BY 1, 2""", e=emp)
    contagem = {f"{l['situacao']}|{l['tipo_venda']}": l["n"] for l in linhas}
    assert contagem == E["composicao"]


def test_unidades_fora_do_vgv_estao_marcadas(s, emp):
    """Unidades que existem no Sienge e ficam fora do estudo, com motivo."""
    fora = q(s, """SELECT nome, motivo_exclusao FROM unidade
                    WHERE empreendimento_id = :e AND NOT considerar_na_viabilidade""",
             e=emp)
    assert {f["nome"] for f in fora} == set(E["fora_do_vgv"])
    assert all(f["motivo_exclusao"] for f in fora), "exclusão sem motivo registrado"


def test_carteira_bate_com_a_planilha(s, emp):
    """Comercial!N207 (recebido) e CH207 (a receber)."""
    rec = q(s, """SELECT sum(valor_liquido) v FROM parcela_recebida p
                    JOIN unidade u ON u.id = p.unidade_id
                   WHERE u.empreendimento_id = :e""", e=emp)[0]["v"]
    car = q(s, """SELECT sum(valor) v FROM parcela_receber p
                    JOIN unidade u ON u.id = p.unidade_id
                   WHERE u.empreendimento_id = :e""", e=emp)[0]["v"]
    assert float(rec) == pytest.approx(E["recebido"], abs=CENT)
    assert float(car) == pytest.approx(E["a_receber"], abs=CENT)


def test_incorrido_importado(s, emp):
    """Fin_Obra: todo lançamento entra, e todo lançamento tem conta."""
    n = q(s, """SELECT count(*) n, count(conta_id) c FROM movimento_realizado
                 WHERE empreendimento_id = :e""", e=emp)[0]
    assert n["n"] == E["movimentos"]
    assert n["c"] == E["movimentos"], "lançamento sem conta some do realizado"


# ---------------------------------------------------------------- DRE
@pytest.mark.parametrize("campo", sorted(E.get("dre", {})))
def test_linha_do_dre(resultado, campo):
    dre, _, _ = resultado
    alvo, celula, tol = E["dre"][campo]
    assert getattr(dre, campo) == pytest.approx(alvo, abs=tol), f"planilha: {celula}"


def test_margem(resultado):
    dre, _, _ = resultado
    assert dre.margem == pytest.approx(E["margem"], abs=1e-5), "SIMULAÇÕES N54"


# ---------------------------------------------------------------- indicadores
def test_custo_por_m2(resultado):
    _, _, ind = resultado
    assert ind.custo_m2_privativa == pytest.approx(E["custo_m2"], abs=0.001), "F36"


def test_eficiencia(resultado):
    _, _, ind = resultado
    assert ind.eficiencia == pytest.approx(E["eficiencia"], abs=1e-6), "L59"


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

    Tolerância de R$ 10 sobre R$ 104 M: os pesos da EAP e as curvas são
    gravados com 8 casas, o que deixa centavos de resíduo na recomposição.
    """
    _, fluxo, _ = resultado
    total = sum(fluxo.linha("OBRA").valores.values())
    assert total == pytest.approx(E["obra_total"], abs=10.0)


def test_taxa_de_obra_segue_a_obra(resultado):
    _, fluxo, _ = resultado
    obra = sum(fluxo.linha("OBRA").valores.values())
    taxa = sum(fluxo.linha("TX_OBRA").valores.values())
    assert taxa == pytest.approx(obra * 0.10, abs=REAL)


def test_conservacao_da_carteira(s, cenario_realista):
    """
    Nenhum real da carteira projetada pode sumir: a soma do fluxo de estoque
    tem de bater com o VGV das coortes quando o horizonte é longo o bastante.
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
                               mes_chaves=D(E["mes_chaves"]))
    assert sum(fluxo.values()) == pytest.approx(total, abs=REAL)


def test_exposicao_de_caixa_e_negativa_e_datada(resultado):
    _, _, ind = resultado
    assert ind.exposicao_maxima < 0, "projeto de incorporação sem exposição é suspeito"
    assert ind.mes_exposicao is not None
    assert ind.aporte_necessario == pytest.approx(abs(ind.exposicao_maxima), abs=CENT)


# ---------------------------------------------------------------- cenários
@pytest.mark.parametrize("nome", sorted(E.get("lucro_cenarios", {})))
def test_lucro_dos_tres_cenarios(s, emp, nome):
    alvo, celula = E["lucro_cenarios"][nome]
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
@pytest.mark.parametrize("linha", sorted(E.get("realizado_ad", {})))
def test_realizado_bate_com_a_planilha(s, emp, linha):
    """A coluna AD do fluxo mensal, onde o cache do Excel bate com a fonte."""
    v = {l["linha"]: l["realizado"] for l in visao_viabilidade(s, emp)}
    assert v[linha] == pytest.approx(E["realizado_ad"][linha], abs=REAL)


def test_terreno_realizado_corrige_cache_velho_da_planilha(s, emp):
    """
    A planilha mostra R$ 3,21 M de terreno pago (VM AD35), mas a soma dos
    próprios lançamentos do Sienge dá R$ 1,86 M: a célula J35 tem um SUMIFS
    cujo cache ficou R$ 1,35 M acima da fonte e nunca foi recalculado.
    O sistema lê a fonte, então diverge — de propósito.
    """
    v = {l["linha"]: l["realizado"] for l in visao_viabilidade(s, emp)}
    assert v["(-) Terreno - Pagamento"] == pytest.approx(
        E["terreno_pela_fonte"], abs=REAL)


def test_comissao_realizada_respeita_a_data_de_corte(s, emp):
    """
    A planilha soma a comissão de todas as unidades vendidas sem olhar a data
    do contrato (VM AD13) — inclusive um contrato assinado depois do
    fechamento do mês. Aqui a data de corte vale para tudo.
    """
    julho = {l["linha"]: l["realizado"]
             for l in visao_viabilidade(s, emp, ate=dt.date(2026, 7, 31))}
    agosto = {l["linha"]: l["realizado"]
              for l in visao_viabilidade(s, emp, ate=dt.date(2026, 8, 31))}
    assert julho["(-) Comissão s/ vendas"] == pytest.approx(
        E["comissao_ate_julho"], abs=REAL)
    assert agosto["(-) Comissão s/ vendas"] == pytest.approx(
        E["comissao_ate_agosto"], abs=REAL)


# ---------------------------------------------------------------- correção
def _com_correcao(s, cenario_id, projetado_aa=0.065, obra=True):
    q(s, """UPDATE cenario SET indice_ate_chaves = 'INCC-DI',
                               indice_apos_chaves = 'IGP-M' WHERE id = :c""",
      c=cenario_id)
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
             AND chave IN ('indice_projetado_aa', 'corrigir_custo_obra')""",
      c=cenario_id)
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
    _com_correcao(s, cenario_realista)
    p = carregar_entradas(s, cenario_realista).premissas
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
    assert sum(fluxo.linha("CORRECAO").valores.values()) > 0
    assert sum(fluxo.linha("ESTOQUE").valores.values()) > 0, "a base não pode sumir"
    _sem_correcao(s, cenario_realista)


def test_indice_corrige_os_dois_lados(s, cenario_realista):
    """
    Corrigir só a carteira inventa lucro: o INCC que reajusta a parcela do
    cliente é o mesmo que encarece o concreto.
    """
    _, fluxo, _ = _com_correcao(s, cenario_realista, obra=True)
    assert sum(fluxo.linha("CORRECAO_OBRA").valores.values()) < 0
    _sem_correcao(s, cenario_realista)


def test_projecao_nominal_subestima_a_exposicao(s, cenario_realista):
    """
    O achado que justifica a funcionalidade: com INCC nos dois lados a
    exposição de caixa PIORA, porque o custo infla durante a obra e a
    correção da carteira só entra depois, diluída nas parcelas e nas chaves.
    Projetar em valores nominais subestima a necessidade de aporte.
    """
    _sem_correcao(s, cenario_realista)
    _, _, nominal = calcular(carregar_entradas(s, cenario_realista))
    _, _, corrigido = _com_correcao(s, cenario_realista, obra=True)
    assert corrigido.exposicao_maxima < nominal.exposicao_maxima
    _sem_correcao(s, cenario_realista)
