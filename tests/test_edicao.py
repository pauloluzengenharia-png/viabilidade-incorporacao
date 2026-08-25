"""
Conferências dos módulos de entrada de dados.

O que importa aqui não é o SQL — é a promessa que os módulos fazem: só grava o
que mudou, registra quem mudou, e as travas do estudo continuam valendo mesmo
quando a edição vem pela tela.
"""
import datetime as dt

import pytest

from app import edicao


# ------------------------------------------------------------- leitura
@pytest.mark.parametrize("bruto,esperado", [
    ("0,045", 0.045),
    ("0.045", 0.045),
    ("18.228,66", 18228.66),
    ("18228.66", 18228.66),
    ("1.234.567,89", 1234567.89),
    ("", 0.0),
    (None, 0.0),
    ("  12  ", 12.0),
    ("não é número", 0.0),
    ("-1.500,50", -1500.50),
    ("1 234,56", 1234.56),
])
def test_numero_aceita_o_jeito_daqui_de_digitar(bruto, esperado):
    assert edicao.num(bruto) == pytest.approx(esperado)


@pytest.mark.parametrize("bruto,esperado", [
    ("250.000", 250_000),      # milhar: um ponto, três dígitos, inteiro não-zero
    ("1.500", 1_500),
    ("2.200.000", 2_200_000),  # dois pontos: sempre milhar
    ("0.045", 0.045),          # parte inteira zero: decimal
    ("0.000", 0.0),
    ("0.5", 0.5),              # menos de três dígitos: decimal
    ("18228.66", 18228.66),
])
def test_ponto_sem_virgula_e_o_caso_ambiguo(bruto, esperado):
    """
    `250.000` pode ser duzentos e cinquenta mil ou duzentos e cinquenta. Errar
    isso é como um orçamento de marketing de R$ 250 entra num estudo — foi
    exatamente o que aconteceu na primeira versão desta tela.
    """
    assert edicao.num(bruto) == pytest.approx(esperado)


def test_data_invalida_vira_nada_em_vez_de_estourar():
    assert edicao.data("2026-07-31") == dt.date(2026, 7, 31)
    assert edicao.data("31/07/2026") is None
    assert edicao.data("") is None


# ------------------------------------------------------------- diferença
def test_so_e_diferente_o_que_mudou_de_verdade():
    assert not edicao.diferentes(0.18, 0.18)
    assert not edicao.diferentes(0.18, 0.18 + 1e-12)   # ruído de ponto flutuante
    assert edicao.diferentes(0.18, 0.15)
    assert not edicao.diferentes(None, "")             # campo que nunca existiu
    assert not edicao.diferentes("", None)
    assert edicao.diferentes(None, "INCC-DI")
    assert edicao.diferentes("INCC-DI", "")


def test_como_texto_e_legivel_no_historico():
    assert edicao.como_texto(0.18) == "0.18"
    assert edicao.como_texto(0.0) == "0"
    assert edicao.como_texto(True) == "sim"
    assert edicao.como_texto(False) == "não"
    assert edicao.como_texto(None) is None


# ------------------------------------------------------------- definição
def test_todo_campo_dos_modulos_tem_rotulo_e_tipo_conhecido():
    tipos = {"numero", "inteiro", "texto", "data", "escolha"}
    for mod in edicao.MODULOS.values():
        assert mod.campos, f"{mod.slug} não tem campo nenhum"
        for c in mod.campos:
            assert c.rotulo.strip(), f"{mod.slug}.{c.chave} sem rótulo"
            assert c.tipo in tipos, f"{mod.slug}.{c.chave} com tipo {c.tipo}"
            if c.tipo == "escolha":
                assert c.opcoes, f"{mod.slug}.{c.chave} é escolha sem opções"


def test_a_tabela_de_venda_declara_quem_precisa_fechar_cem():
    assert edicao.COMERCIAL.soma_cem
    chaves = {c.chave for c in edicao.COMERCIAL.campos}
    assert set(edicao.COMERCIAL.soma_cem) <= chaves


def test_toda_premissa_editavel_sabe_em_que_unidade_e_guardada():
    """A tabela `premissa` exige a unidade; um campo sem ela quebra na gravação."""
    guardadas = [c.chave for c in edicao.PREMISSAS.campos
                 if c.chave not in edicao.NO_CENARIO]
    faltando = [k for k in guardadas if k not in edicao.UNIDADE_DA_PREMISSA]
    assert not faltando, f"sem unidade declarada: {faltando}"


def test_o_glossario_explica_todo_campo_dos_modulos():
    """Um campo sem verbete é um '?' que não abre — pior que não ter o '?'."""
    sem = [f"{m.slug}.{c.chave}" for m in edicao.MODULOS.values()
           for c in m.campos if c.verbete is None]
    assert not sem, sem
