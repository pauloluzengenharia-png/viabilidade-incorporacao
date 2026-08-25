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
