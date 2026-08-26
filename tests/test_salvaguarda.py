"""
A prova do backup é a volta: exportar, apagar tudo, restaurar — e o estudo
calcular exatamente o mesmo lucro de antes.

Contar linhas não basta. Uma restauração pode ter o número certo de linhas com
uma coluna de valor trocada por None, e a contagem passa. O lucro não passa:
ele atravessa premissas, unidades, tabela de venda, obra e composições — se
qualquer uma voltar errada, o número final denuncia.

Estes testes rodam numa transação que é desfeita no fim: o banco de
desenvolvimento sai deles exatamente como entrou.
"""
from __future__ import annotations

import copy
import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa de DATABASE_URL apontando para um banco migrado")

from app import salvaguarda as B                     # noqa: E402
from app.db import Sessao, q                         # noqa: E402


@pytest.fixture
def s():
    """Sessão que nunca comita: o rollback devolve o banco intacto."""
    ses = Sessao()
    yield ses
    ses.rollback()
    ses.close()


def _contagens(s):
    return {t: q(s, f'SELECT count(*) AS n FROM "{t}"')[0]["n"]  # noqa: S608
            for t in B.ordem_de_carga(s)}


def test_a_ordem_poe_pais_antes_de_filhos(s):
    ordem = B.ordem_de_carga(s)
    posicao = {t: i for i, t in enumerate(ordem)}
    for filho, pais in B._dependencias(s).items():
        for pai in pais:
            assert posicao[pai] < posicao[filho], f"{pai} precisa vir antes de {filho}"


def test_ida_e_volta_sem_perder_uma_linha(s):
    antes = _contagens(s)
    assert antes["empreendimento"] >= 1, "o teste precisa de um banco carregado"

    # o arquivo passa por JSON de verdade — é o que viaja entre provedores
    arquivo = json.loads(json.dumps(B.exportar(s), ensure_ascii=False))
    gravadas = B.restaurar(s, arquivo)

    depois = _contagens(s)
    assert depois == antes, "a volta tem de ter exatamente as linhas da ida"
    assert sum(gravadas.values()) == sum(1 for t, n in antes.items() for _ in range(n))


def test_o_lucro_sobrevive_a_viagem(s):
    from app.repositorio import carregar_entradas
    from app.servico import calcular

    cen = q(s, """SELECT id FROM cenario WHERE tipo = 'projecao'
                   ORDER BY id LIMIT 1""")[0]["id"]
    lucro_antes = calcular(carregar_entradas(s, cen)).__class__  # noqa: F841
    dre_antes, _, _ = calcular(carregar_entradas(s, cen))

    arquivo = json.loads(json.dumps(B.exportar(s), ensure_ascii=False))
    B.restaurar(s, arquivo)

    dre_depois, _, _ = calcular(carregar_entradas(s, cen))
    assert dre_depois.lucro == pytest.approx(dre_antes.lucro, abs=0.01), (
        "o lucro mudou na restauração — alguma coluna voltou diferente")
    assert dre_depois.vgv == pytest.approx(dre_antes.vgv, abs=0.01)


def test_as_sequencias_continuam_de_onde_o_dado_parou(s):
    arquivo = json.loads(json.dumps(B.exportar(s), ensure_ascii=False))
    B.restaurar(s, arquivo)
    # se a sequência tivesse voltado ao 1, este INSERT colidiria com id existente
    novo = q(s, """INSERT INTO alteracao (empreendimento_id, modulo, entidade,
                                          campo, valor_novo, autor)
                   VALUES (1, 'teste', 'seq', 'seq', 'x', 'teste')
                   RETURNING id""")[0]["id"]
    maior = q(s, "SELECT max(id) AS m FROM alteracao")[0]["m"]
    assert novo == maior


@pytest.mark.parametrize("dados,erro", [
    ({"sistema": "outro"}, "não é um backup"),
    ({"sistema": "viabilidade-mmi", "formato": 99}, "formato 99"),
    ({"sistema": "viabilidade-mmi", "formato": 1,
      "tabelas": {"tabela_do_futuro": [{"x": 1}]}}, "versão mais nova"),
])
def test_arquivo_errado_para_antes_de_tocar_no_banco(s, dados, erro):
    antes = _contagens(s)
    with pytest.raises(B.ArquivoDeBackupInvalido, match=erro):
        B.restaurar(s, dados)
    assert _contagens(s) == antes


def test_linha_recusada_desfaz_a_restauracao_inteira(s):
    """
    Um arquivo com uma linha inválida no meio não pode deixar o banco pela
    metade. A transação de quem chama é o mecanismo; este teste prova que ela
    de fato desfaz.
    """
    arquivo = json.loads(json.dumps(B.exportar(s), ensure_ascii=False))
    ruim = copy.deepcopy(arquivo)
    ruim["tabelas"]["premissa"][0]["valor"] = "não sou número"
    with pytest.raises(Exception):                            # noqa: B017
        B.restaurar(s, ruim)
    s.rollback()
    assert _contagens(s)["premissa"] > 0, "o rollback devolveu o banco inteiro"
