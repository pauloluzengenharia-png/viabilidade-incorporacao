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


# ------------------------------------------------------------- formatação
@pytest.mark.parametrize("valor,esperado", [
    (3704512.5, "3.704.512,50"),
    (30750.0, "30.750,00"),
    (0, "0,00"),
    (117.95, "117,95"),
    (-1500.5, "-1.500,50"),
    (104048246, "104.048.246,00"),
    (1545045.14, "1.545.045,14"),
    (999.999, "999,999"),
])
def test_dinheiro_escrito_como_dinheiro_se_escreve(valor, esperado):
    assert edicao.moeda(valor) == esperado


def test_formatar_nunca_apaga_casa_decimal():
    """
    O preço por m² da Kiev tem quatro casas porque veio de uma divisão.
    Exibir com duas faria a próxima gravação arredondar de verdade — a tela
    passaria a mudar o dado só de ser aberta.
    """
    assert edicao.moeda(18228.6645) == "18.228,6645"
    assert edicao.num(edicao.moeda(18228.6645)) == pytest.approx(18228.6645)


@pytest.mark.parametrize("valor", [3704512.5, 18228.6645, 104048246, 0.01, -1500.5])
def test_dinheiro_vai_e_volta_sem_perder_nada(valor):
    assert edicao.num(edicao.moeda(valor)) == pytest.approx(valor)


def test_le_valor_com_cifrao_e_por_cento_colados():
    """Copiar de uma planilha traz o R$ junto; recusar isso seria implicância."""
    assert edicao.num("R$ 250.000,00") == pytest.approx(250_000)
    assert edicao.num("R$3.704.512,50") == pytest.approx(3_704_512.50)
    assert edicao.num("4,5%") == pytest.approx(4.5)


def test_percentual_continua_em_fracao():
    assert edicao.percentual(0.045) == "0,045"
    assert edicao.percentual(0.0) == "0"
    assert edicao.percentual(0.18) == "0,18"


def test_campo_deduz_o_formato_do_sufixo():
    assert edicao.Campo("x", "X", "numero", "R$").formato == "moeda"
    assert edicao.Campo("x", "X", "numero", "R$/m²").formato == "moeda"
    assert edicao.Campo("x", "X", "numero", "% da receita").formato == "percentual"
    assert edicao.Campo("x", "X", "numero", "meses").formato == ""


def test_percentual_nao_ganha_adorno():
    """Um '%' colado num campo escrito 0,06 leria como 0,06 por cento."""
    assert edicao.Campo("x", "X", "numero", "% do bruto").adorno == ""
    assert edicao.Campo("x", "X", "numero", "R$").adorno == "R$"


def test_todo_campo_em_reais_dos_modulos_esta_marcado_como_moeda():
    faltando = [f"{m.slug}.{c.chave}" for m in edicao.MODULOS.values()
                for c in m.campos
                if "R$" in c.sufixo and c.formato != "moeda"]
    assert not faltando, faltando
