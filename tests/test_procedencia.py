"""
Conferências da abertura de linha.

A promessa desta tela é forte: o que ela mostra tem de somar exatamente o que a
tabela mostra. Se a lista de lançamentos não fechar com a coluna Realizado, a
tela vira uma segunda versão da verdade — que é o problema que o sistema inteiro
existe para não ter.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa de DATABASE_URL apontando para o banco migrado")

from app import procedencia                            # noqa: E402
from app.db import Sessao, q                           # noqa: E402
from app.motor.engine import calcular_vgv              # noqa: E402
from app.repositorio import carregar_entradas          # noqa: E402
from app.servico import LINHAS_DRE, calcular, visao_viabilidade  # noqa: E402


@pytest.fixture(scope="module")
def sessao():
    ses = Sessao()
    yield ses
    ses.close()


@pytest.fixture(scope="module")
def kiev(sessao):
    r = q(sessao, "SELECT id FROM empreendimento ORDER BY id LIMIT 1")
    if not r:
        pytest.skip("banco sem empreendimento — rode migrar_kiev.py")
    return r[0]["id"]


@pytest.fixture(scope="module")
def calculado(sessao, kiev):
    cen = q(sessao, """SELECT id FROM cenario
                        WHERE empreendimento_id = :e AND principal""", e=kiev)[0]["id"]
    e = carregar_entradas(sessao, cen)
    dre, fluxo, ind = calcular(e)
    bloco = calcular_vgv(e.unidades, e.premissas, e.tabela)
    return {"emp": kiev, "cenario": cen, "dre": dre, "bloco": bloco,
            "obra": e.obra, "premissas": e.premissas}


def test_toda_linha_do_resultado_tem_formula_ou_e_soma(calculado):
    """
    Uma linha sem fórmula e sem ser subtotal é uma linha que a tela não sabe
    explicar — e é justamente sobre ela que a pergunta vai aparecer.
    """
    subtotais = {"RECEITA LÍQUIDA", "LUCRO", "VGV", "RECEITA C/ VENDAS SPE"}
    sem = []
    for rotulo, _ in LINHAS_DRE:
        f = procedencia.formula_da_linha(rotulo, calculado["dre"], calculado["bloco"],
                                         calculado["obra"], calculado["premissas"])
        if f is None and rotulo not in subtotais:
            sem.append(rotulo)
    assert not sem, sem


def test_a_formula_chega_no_numero_da_tabela(calculado, sessao):
    """
    O resultado descrito na abertura tem de ser o mesmo que a tabela publica.
    Divergir aqui significa que a explicação e o cálculo se separaram.
    """
    tabela = {l["linha"]: l for l in visao_viabilidade(sessao, calculado["emp"])}
    divergentes = []
    for rotulo, _ in LINHAS_DRE:
        f = procedencia.formula_da_linha(rotulo, calculado["dre"], calculado["bloco"],
                                         calculado["obra"], calculado["premissas"])
        if not f:
            continue
        esperado = tabela[rotulo]["atualizado"]
        if abs(f.resultado - esperado) > 0.01:
            divergentes.append((rotulo, f.resultado, esperado))
    assert not divergentes, divergentes


def test_os_lancamentos_somam_a_coluna_realizado(sessao, kiev):
    """
    A soma dos lançamentos listados tem de dar o número da coluna Realizado, em
    toda linha. É a conferência que torna a tela confiável.
    """
    corte = q(sessao, "SELECT mes_corte_realizado AS c FROM empreendimento WHERE id = :e",
              e=kiev)[0]["c"]
    tabela = {l["linha"]: l for l in visao_viabilidade(sessao, kiev, corte)}
    subtotais = {"VGV", "RECEITA C/ VENDAS SPE", "RECEITA LÍQUIDA", "LUCRO"}

    divergentes = []
    for rotulo, _ in LINHAS_DRE:
        if rotulo in subtotais:
            continue        # subtotal é soma de outras linhas, não tem lançamento
        detalhe = procedencia.lancamentos_da_linha(sessao, kiev, rotulo, corte)
        if abs(detalhe["total"] - tabela[rotulo]["realizado"]) > 0.01:
            divergentes.append((rotulo, detalhe["total"], tabela[rotulo]["realizado"]))
    assert not divergentes, divergentes


def test_o_excel_da_formula_referencia_todos_os_operandos(calculado):
    """
    O template da fórmula vira referência de célula na planilha exportada. Um
    `{3}` numa fórmula de dois operandos sairia como texto solto na planilha.
    """
    import re
    ruins = []
    for rotulo, _ in LINHAS_DRE:
        f = procedencia.formula_da_linha(rotulo, calculado["dre"], calculado["bloco"],
                                         calculado["obra"], calculado["premissas"])
        if not f:
            continue
        indices = [int(n) for n in re.findall(r"\{(\d+)\}", f.excel)]
        if any(i > len(f.termos) or i < 1 for i in indices):
            ruins.append((rotulo, f.excel, len(f.termos)))
        if "{todos}" not in f.excel and not indices:
            ruins.append((rotulo, f.excel, "não referencia operando nenhum"))
    assert not ruins, ruins
