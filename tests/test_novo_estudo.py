"""Conferências do cadastro de um estudo do zero."""
import datetime as dt

import pytest

from app.novo_estudo import Estudo, curva, meses_entre, somar_meses


def base() -> Estudo:
    return Estudo(
        nome="Teste", area_privativa=8000, area_construida=16000,
        data_lancamento=dt.date(2026, 10, 1),
        data_entrega_prevista=dt.date(2030, 6, 30),
        unidades=100, preco_m2=12000, custo_raso=60_000_000,
        unidades_por_mes=4, tma_anual=0.18)


@pytest.mark.parametrize("formato", ["linear", "s_suave", "s_acentuada"])
@pytest.mark.parametrize("meses", [1, 2, 12, 40, 61])
def test_curva_soma_exatamente_um(formato, meses):
    """A trigger do banco confere isso. Aqui garantimos que nunca chega errado."""
    c = curva(formato, meses)
    assert len(c) == meses
    assert sum(c) == pytest.approx(1.0, abs=1e-12)
    assert all(x >= 0 for x in c)


def test_curva_s_concentra_o_meio():
    c = curva("s_acentuada", 12)
    assert max(c) == max(c[4:8]), "a curva S tem de ter o pico no miolo da obra"
    assert c[0] < c[5] and c[-1] < c[5]


def test_curva_linear_e_plana():
    c = curva("linear", 10)
    assert all(x == pytest.approx(c[0]) for x in c)


def test_estudo_valido_nao_tem_erro():
    assert base().erros() == []


def test_tabela_que_nao_fecha_cem_e_recusada():
    d = base(); d.chaves = 0.10
    erros = d.erros()
    assert any("100%" in e for e in erros)


def test_construida_menor_que_privativa_e_recusada():
    d = base(); d.area_construida = 1000
    assert any("construída" in e for e in d.erros())


def test_entrega_antes_do_lancamento_e_recusada():
    d = base(); d.data_entrega_prevista = dt.date(2026, 1, 1)
    assert any("depois do lançamento" in e for e in d.erros())


def test_erros_saem_todos_de_uma_vez():
    """Quem preenche 40 campos merece ver os erros juntos."""
    assert len(Estudo().erros()) >= 6


def test_calendario():
    assert meses_entre(dt.date(2026, 1, 31), dt.date(2026, 3, 31)) == 3
    assert somar_meses(dt.date(2026, 1, 15), 0) == dt.date(2026, 1, 31)
    assert somar_meses(dt.date(2026, 11, 1), 2) == dt.date(2027, 1, 31)
    assert somar_meses(dt.date(2026, 12, 1), 1) == dt.date(2027, 1, 31)
