"""
Motor de cálculo da viabilidade de incorporação.

Reproduz, em código, a cadeia de cálculo da planilha:

    Unidades (Sienge)  ─┐
    Contratos/Receber  ─┼─> Comercial ─┬─> SIMULAÇÕES (DRE do cenário)
    Recebido           ─┘              └─> Vendas_<cenário> (coorte de recebíveis)
    Cronograma obra ───────────────────────> Viabilidades Mensais (fluxo de caixa)
                                                     │
                                                     └─> VIABILIDADE (orçado × atualizado × realizado)

Nada aqui depende de Excel: a entrada é `Premissas` + lista de `Unidade`
+ `Obra` + `PlanoVendaMes`, e a saída é um `Resultado` serializável.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from .modelo import (AtividadeObra, Obra, PlanoVendaMes, Premissas, SituacaoUnidade,
                    TabelaVenda, TipoVenda, Unidade)


# ==========================================================================
# utilitários de calendário — o eixo do modelo é sempre o ÚLTIMO dia do mês
# (a planilha usa EOMONTH em todo lugar)
# ==========================================================================

def fim_mes(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    return fim_mes(date(d.year + m // 12, m % 12 + 1, 1))


def eixo_meses(inicio: date, n: int) -> list[date]:
    return [add_meses(fim_mes(inicio), i) for i in range(n)]


# ==========================================================================
# 1. VGV E DRE DO CENÁRIO   (aba SIMULAÇÕES)
# ==========================================================================

@dataclass
class BlocoVGV:
    vendida: float          # M5  — unidades já vendidas, a valor de contrato
    permuta: float          # M9  — unidades entregues ao terreneiro
    disponivel_normal: float   # M11
    disponivel_leal: float     # M12
    disponivel_investidor: float  # M13
    recebido_disponivel: float    # Σ Comercial!N de unidades ainda "Disponível"
    comissao_estoque: float       # M11 × %comissão
    comissao_vendida: float       # Σ Comercial!E das vendidas

    @property
    def disponivel(self) -> float:            # M10
        return self.disponivel_normal + self.disponivel_leal + self.disponivel_investidor

    @property
    def deducoes_vgv(self) -> float:          # M19 (negativo)
        return -(self.comissao_estoque + self.comissao_vendida)

    @property
    def receita_spe(self) -> float:           # M23
        return (self.vendida + self.permuta + self.disponivel
                + self.recebido_disponivel - self.comissao_estoque)

    @property
    def vgv(self) -> float:                   # M17 = M23 − M19
        return self.receita_spe - self.deducoes_vgv


def calcular_vgv(unidades: Iterable[Unidade], p: Premissas,
                 tabela: TabelaVenda) -> BlocoVGV:
    """SIMULAÇÕES linhas 5..23."""
    un = list(unidades)

    def soma(campo, **filtros) -> float:
        tot = 0.0
        for u in un:
            if all(getattr(u, k) == v for k, v in filtros.items()):
                tot += campo(u) or 0.0
        return tot

    vgv_un = lambda u: (u.recebido + u.a_receber_total
                        + u.estoque(p.preco_m2_estoque, p.usar_preco_tabela)
                        + u.permuta_valor)                       # Comercial!CR

    vendida = sum(vgv_un(u) for u in un
                  if u.situacao == SituacaoUnidade.VENDIDA
                  and u.tipo_venda in (TipoVenda.NORMAL, TipoVenda.INVESTIDOR,
                                       TipoVenda.GARAGEM))
    permuta = sum(vgv_un(u) for u in un if u.situacao == SituacaoUnidade.PERMUTA)

    def disp(tipo: TipoVenda) -> float:
        """
        M11/M12/M13 — estoque precificado.
        O cenário realista usa o preço de tabela já cadastrado por unidade;
        otimista/pessimista repreçam a área pelo R$/m² do cenário.
        O lote do investidor tem preço próprio (M13/L13), independente do estoque.
        """
        tot = 0.0
        for u in un:
            if u.situacao != SituacaoUnidade.DISPONIVEL or u.tipo_venda != tipo:
                continue
            if p.usar_preco_tabela:
                tot += u.valor_bruto
            elif tipo == TipoVenda.INVESTIDOR:
                tot += p.preco_investidor_unidade
            else:
                tot += u.area_privativa * p.preco_m2_estoque
        return tot

    dn, dl, di = disp(TipoVenda.NORMAL), disp(TipoVenda.LEAL), disp(TipoVenda.INVESTIDOR)

    return BlocoVGV(
        vendida=vendida,
        permuta=permuta,
        disponivel_normal=dn,
        disponivel_leal=dl,
        disponivel_investidor=di,
        # SUMIFS(Comercial!N;N>1;G="Disponível") — sinal de venda em andamento
        recebido_disponivel=soma(lambda u: u.recebido if (u.recebido or 0) > 1 else 0,
                                 situacao=SituacaoUnidade.DISPONIVEL),
        comissao_estoque=dn * tabela.comissao,
        comissao_vendida=soma(lambda u: u.comissao, situacao=SituacaoUnidade.VENDIDA),
    )


@dataclass
class DRE:
    """SIMULAÇÕES linhas 17..54 / VIABILIDADE coluna Q."""
    vgv: float
    desconto: float
    comissao: float
    receita_spe: float
    ret: float
    distratos: float
    despesas_comerciais: float
    receita_liquida: float
    terreno_permuta: float
    terreno_pagamento: float
    terreno_registro: float
    obra_custo_raso: float
    taxa_adm_obra: float
    taxa_viabilizacao: float
    decoracao: float
    projetos_e_outros: float
    marketing_stand: float
    marketing_propaganda: float
    outras_desp_adm: float
    outras_entradas: float
    receitas_financiamento: float = 0.0
    amortizacao_financiamento: float = 0.0
    juros_financiamento: float = 0.0
    retencoes: float = 0.0

    @property
    def deducoes_vgv(self) -> float:
        return self.desconto + self.comissao

    @property
    def deducoes_receita(self) -> float:
        return self.ret + self.distratos + self.despesas_comerciais

    @property
    def despesas(self) -> float:
        return (self.decoracao + self.projetos_e_outros + self.marketing_stand
                + self.marketing_propaganda + self.outras_desp_adm + self.outras_entradas)

    @property
    def gastos(self) -> float:
        return (self.terreno_permuta + self.terreno_pagamento + self.terreno_registro
                + self.obra_custo_raso + self.taxa_adm_obra + self.taxa_viabilizacao
                + self.despesas)

    @property
    def lucro_antes_financeiro(self) -> float:
        return self.receita_liquida + self.gastos

    @property
    def resultado_financeiro(self) -> float:
        return (self.receitas_financiamento + self.amortizacao_financiamento
                + self.juros_financiamento + self.retencoes)

    @property
    def lucro(self) -> float:
        return self.lucro_antes_financeiro + self.resultado_financeiro

    @property
    def margem(self) -> float:
        return self.lucro / self.receita_liquida if self.receita_liquida else 0.0

    def como_percentual_vv(self) -> dict[str, float]:
        """A coluna %VV da planilha: tudo dividido pela RECEITA LÍQUIDA."""
        base = self.receita_liquida or 1.0
        return {k: v / base for k, v in self.__dict__.items() if isinstance(v, (int, float))}


def calcular_dre(bloco: BlocoVGV, obra: Obra, p: Premissas) -> DRE:
    """SIMULAÇÕES linhas 25..54."""
    receita_spe = bloco.receita_spe
    terreno_pgto = -sum(p.terreno_parcelas)
    custo_raso = -abs(obra.custo_raso)

    return DRE(
        vgv=bloco.vgv,
        desconto=0.0,
        comissao=bloco.deducoes_vgv,
        receita_spe=receita_spe,
        ret=-receita_spe * p.ret,
        distratos=-bloco.vgv * p.distratos,
        despesas_comerciais=-p.despesas_comerciais,
        receita_liquida=receita_spe
        + (-receita_spe * p.ret) + (-bloco.vgv * p.distratos) + (-p.despesas_comerciais),
        terreno_permuta=-bloco.permuta - bloco.disponivel_leal,
        terreno_pagamento=terreno_pgto,
        terreno_registro=terreno_pgto * p.terreno_registro_perc,
        obra_custo_raso=custo_raso,
        taxa_adm_obra=custo_raso * p.taxa_adm_obra,
        taxa_viabilizacao=-receita_spe * p.taxa_viabilizacao,
        decoracao=-p.decoracao,
        projetos_e_outros=-p.projetos_e_outros,
        marketing_stand=-p.marketing_stand,
        marketing_propaganda=-p.marketing_propaganda,
        outras_desp_adm=custo_raso * p.outras_desp_adm_perc,
        outras_entradas=p.outras_entradas,
    )


# ==========================================================================
# 2. MOTOR DE RECEBÍVEIS DO ESTOQUE   (abas Vendas_realista/pessimista/otimista)
# ==========================================================================

@dataclass
class Coorte:
    """
    Uma linha-bloco das abas Vendas_*: as unidades vendidas em um mês,
    com a mesma tabela de venda. 6 linhas por coorte (Ato/Mensal/Anual/
    Semestral/CH-FI) que se espalham no eixo do tempo.
    """
    mes_venda: date
    indice_mes: int          # Vendas!B — 1 = mes_base
    quantidade: int          # Vendas!E
    tipo: TipoVenda          # Vendas!F
    valor_unitario: float    # Vendas!I$1 (Padrão) ou I$2 (Investidor), líquido de comissão


def gerar_coortes(plano: Iterable[PlanoVendaMes], p: Premissas,
                  valor_unidade_padrao: float) -> list[Coorte]:
    base = fim_mes(p.mes_base)
    saida = []
    for item in plano:
        m = fim_mes(item.mes)
        if m < base or not item.quantidade:
            continue
        idx = (m.year - base.year) * 12 + (m.month - base.month) + 1
        valor = (p.preco_investidor_unidade if item.tipo == TipoVenda.INVESTIDOR
                 else valor_unidade_padrao)
        saida.append(Coorte(m, idx, item.quantidade, item.tipo, valor))
    return saida


def recebiveis_estoque(coortes: Iterable[Coorte], tabela: TabelaVenda,
                       p: Premissas, meses: list[date],
                       mes_chaves: Optional[date] = None,
                       prazos_por_tipo: Optional[dict[TipoVenda, dict]] = None
                       ) -> dict[date, float]:
    """
    Vendas_*!N6:CK456 → linha 457 (TOTAL).

    Regras, uma por componente da tabela de venda:
      Ato        → 1 parcela no próprio mês da venda            (Vendas!N6)
      Mensal     → n parcelas iguais a partir do mês da venda   (Vendas!N7/N13)
      Semestral  → parcela a cada 6 meses contados da venda     (Vendas!N9/N15)
      Anual      → parcela a cada 12 meses contados da venda    (Vendas!N8/N14)
      CH/FI      → 1 parcela no mês da entrega das chaves       (Vendas!N10/N16)
    A soma de todos os componentes é o valor da unidade líquido de comissão.
    """
    idx_de = {m: i + 1 for i, m in enumerate(meses)}
    fluxo = {m: 0.0 for m in meses}
    perc = tabela.perc_liquidos
    prazos_por_tipo = prazos_por_tipo or {}
    idx_chaves = idx_de.get(fim_mes(mes_chaves)) if mes_chaves else None

    for c in coortes:
        pr = prazos_por_tipo.get(c.tipo, {})
        n_mensais = pr.get("n_mensais", tabela.n_mensais)
        total_coorte = c.valor_unitario * c.quantidade

        comp = {
            "ato":       (perc["ato"],       1,                       c.indice_mes, 1),
            "mensal":    (perc["mensal"],    n_mensais,               c.indice_mes, 1),
            "anual":     (perc["anual"],     max(n_mensais // 12, 1), c.indice_mes + 11, 12),
            "semestral": (perc["semestral"], max(n_mensais // 6, 1),  c.indice_mes + 5, 6),
        }
        for _, (pc, n, inicio, passo) in comp.items():
            if not pc or not n:
                continue
            valor_parcela = total_coorte * pc / n
            for k in range(n):
                i = inicio + k * passo
                if 1 <= i <= len(meses):
                    fluxo[meses[i - 1]] += valor_parcela

        # chaves: entra no mês da entrega, não no prazo da coorte
        if perc["chaves"] and idx_chaves:
            fluxo[meses[idx_chaves - 1]] += total_coorte * perc["chaves"]

    return fluxo


# ==========================================================================
# 3. FLUXO DE CAIXA MENSAL   (aba Viabilidades Mensais_Cenarios)
# ==========================================================================

@dataclass
class LinhaFluxo:
    """Uma linha do fluxo mensal, com o valor por mês."""
    codigo: str
    descricao: str
    grupo: str                                # RECEITA | DEDUCAO | GASTO | FINANCEIRO | APORTE
    valores: dict[date, float] = field(default_factory=dict)

    def total(self) -> float:
        return sum(self.valores.values())


@dataclass
class FluxoMensal:
    meses: list[date]
    linhas: list[LinhaFluxo]

    def linha(self, codigo: str) -> LinhaFluxo:
        for l in self.linhas:
            if l.codigo == codigo:
                return l
        raise KeyError(codigo)

    def serie(self, grupos: tuple[str, ...]) -> dict[date, float]:
        out = {m: 0.0 for m in self.meses}
        for l in self.linhas:
            if l.grupo in grupos:
                for m, v in l.valores.items():
                    out[m] = out.get(m, 0.0) + v
        return out

    # --- agregados que a planilha calcula nas linhas 15/26/31/33/184/208/218/219 ---
    def receita_bruta(self) -> dict[date, float]:
        return self.serie(("RECEITA",))

    def receita_liquida(self) -> dict[date, float]:
        r, d = self.receita_bruta(), self.serie(("DEDUCAO",))
        return {m: r[m] + d[m] for m in self.meses}

    def lucro_liquido(self) -> dict[date, float]:
        rl = self.receita_liquida()
        g = self.serie(("GASTO", "FINANCEIRO"))
        return {m: rl[m] + g[m] for m in self.meses}

    def movimento(self) -> dict[date, float]:
        ll, ap = self.lucro_liquido(), self.serie(("APORTE",))
        return {m: ll[m] + ap[m] for m in self.meses}

    def saldo_caixa(self, saldo_inicial: float = 0.0) -> dict[date, float]:
        acc, mov, out = saldo_inicial, self.movimento(), {}
        for m in self.meses:
            acc += mov[m]
            out[m] = acc
        return out


def montar_fluxo(unidades: Iterable[Unidade], obra: Obra, p: Premissas,
                 tabela: TabelaVenda, plano: Iterable[PlanoVendaMes],
                 horizonte: int = 90,
                 receb_vendidos: Optional[dict[date, float]] = None,
                 valor_unidade_padrao: Optional[float] = None,
                 mes_chaves: Optional[date] = None,
                 prazos_por_tipo: Optional[dict] = None) -> FluxoMensal:
    """
    Monta o fluxo mês a mês da SPE.

    `receb_vendidos` é o cronograma real de recebíveis das unidades JÁ vendidas
    (na planilha vem do Sienge, Comercial linha 207). Quando ausente, o motor
    projeta essas unidades pela mesma tabela de venda do estoque.
    """
    meses = eixo_meses(p.mes_base, horizonte)
    zero = {m: 0.0 for m in meses}
    L: list[LinhaFluxo] = []

    # ---------- RECEITAS ----------
    vend = dict(zero)
    if receb_vendidos:
        for m, v in receb_vendidos.items():
            m = fim_mes(m)
            if m in vend:
                vend[m] += v
    L.append(LinhaFluxo("1010101", "Venda de imóveis — carteira vendida", "RECEITA", vend))

    vu = valor_unidade_padrao
    if vu is None:
        # SIMULAÇÕES M70/M78: valor médio da unidade de estoque, líquido de comissão
        disp = [u for u in unidades if u.situacao == SituacaoUnidade.DISPONIVEL
                and u.tipo_venda == TipoVenda.NORMAL]
        bruto = sum((u.valor_bruto if p.usar_preco_tabela
                     else u.area_privativa * p.preco_m2_estoque) for u in disp)
        vu = (bruto / len(disp)) * (1 - tabela.comissao) if disp else 0.0

    coortes = gerar_coortes(plano, p, vu)
    est = recebiveis_estoque(coortes, tabela, p, meses, mes_chaves, prazos_por_tipo)
    L.append(LinhaFluxo("ESTOQUE", "Venda de imóveis — estoque projetado", "RECEITA", est))

    # saldo pós-chaves: o que não coube no horizonte é diluído em N meses
    # (VM linhas 22 e 24: C22 = resíduo / E22, liberado após a linha zerar)
    def pos_chaves(serie: dict[date, float], residuo: float, cod: str, desc: str):
        if residuo <= 0:
            return LinhaFluxo(cod, desc, "RECEITA", dict(zero))
        ultimos = [m for m in meses if serie.get(m, 0)]
        inicio = meses.index(max(ultimos)) + 1 if ultimos else 0
        v = dict(zero)
        for k in range(p.meses_pos_chaves):
            if inicio + k < len(meses):
                v[meses[inicio + k]] = residuo / p.meses_pos_chaves
        return LinhaFluxo(cod, desc, "RECEITA", v)

    total_a_receber = sum(u.a_receber_total for u in unidades)
    L.append(pos_chaves(vend, total_a_receber - sum(vend.values()),
                        "POS_CHAVES_V", "Vendido — pós-chaves"))
    total_estoque = sum(c.valor_unitario * c.quantidade for c in coortes)
    L.append(pos_chaves(est, total_estoque - sum(est.values()),
                        "POS_CHAVES_E", "Estoque — pós-chaves"))

    receita = {m: vend[m] + est[m] for m in meses}
    for l in L[-2:]:
        for m in meses:
            receita[m] += l.valores[m]

    # ---------- DEDUÇÕES (defasadas 1 mês: incidem sobre a receita do mês anterior) ----
    def defasada(base: dict[date, float], taxa: float, sinal=-1) -> dict[date, float]:
        v = dict(zero)
        for i, m in enumerate(meses):
            if i:
                v[m] = sinal * base[meses[i - 1]] * taxa
        return v

    L.append(LinhaFluxo("IMPOSTOS", "(-) Impostos sobre receita (RET)", "DEDUCAO",
                        defasada(receita, p.ret)))
    L.append(LinhaFluxo("DESP_COM", "(-) Despesas comerciais", "DEDUCAO",
                        {**zero, meses[0]: -p.despesas_comerciais}))

    # ---------- GASTOS ----------
    # obra: distribuída pela curva do cronograma
    curva = obra.curva_agregada
    if not curva and obra.atividades:
        curva = {}
        total = sum(a.valor for a in obra.atividades) or 1.0
        for a in obra.atividades:
            for m, pc in a.curva.items():
                curva[fim_mes(m)] = curva.get(fim_mes(m), 0.0) + pc * a.valor / total
    obra_mes = dict(zero)
    for m, pc in (curva or {}).items():
        m = fim_mes(m)
        if m in obra_mes:
            obra_mes[m] += -abs(obra.custo_raso) * pc
    L.append(LinhaFluxo("OBRA", "(-) Obra — custo raso", "GASTO", obra_mes))
    L.append(LinhaFluxo("TX_OBRA", "(-) Taxa de administração da obra", "GASTO",
                        defasada(obra_mes, p.taxa_adm_obra, sinal=+1)))
    L.append(LinhaFluxo("TX_CART", "(-) Taxa de administração de carteira", "GASTO",
                        defasada(receita, p.taxa_viabilizacao)))

    # terreno: parcelas em meses consecutivos a partir do mês base
    terreno = dict(zero)
    for i, parc in enumerate(p.terreno_parcelas):
        if i < len(meses):
            terreno[meses[i]] = -parc
    L.append(LinhaFluxo("TERRENO", "(-) Terreno — pagamento", "GASTO", terreno))
    L.append(LinhaFluxo("TERR_REG", "(-) Terreno — registro e outros", "GASTO",
                        {**zero, meses[0]: -sum(p.terreno_parcelas) * p.terreno_registro_perc}))

    # despesas diluídas linearmente sobre a janela em que fazem sentido
    def diluir(total: float, inicio: int, n: int, cod: str, desc: str) -> LinhaFluxo:
        v = dict(zero)
        for k in range(n):
            if inicio + k < len(meses):
                v[meses[inicio + k]] = -total / n
        return LinhaFluxo(cod, desc, "GASTO", v)

    n_obra = sum(1 for m in meses if obra_mes[m])
    L.append(diluir(p.decoracao, max(n_obra - 6, 0), 6, "DECOR", "(-) Incorporação — decoração"))
    L.append(diluir(p.projetos_e_outros, 0, max(n_obra, 1), "PROJ", "(-) Incorporação — projetos e outros"))
    L.append(diluir(p.marketing_stand, 0, 6, "STAND", "(-) Marketing — stand"))
    L.append(diluir(p.marketing_propaganda, 0, max(n_obra, 1), "PROP", "(-) Marketing — propaganda"))
    L.append(LinhaFluxo("OUTRAS_ADM", "(-) Outras despesas administrativas", "GASTO",
                        {m: obra_mes[m] * p.outras_desp_adm_perc for m in meses}))

    fluxo = FluxoMensal(meses, L)

    # ---------- FINANCIAMENTO À PRODUÇÃO ----------
    if p.financiamento_limite:
        L.extend(_linhas_financiamento(fluxo, obra_mes, p, meses))

    return FluxoMensal(meses, L)


def _linhas_financiamento(fluxo: FluxoMensal, obra_mes: dict[date, float],
                          p: Premissas, meses: list[date]) -> list[LinhaFluxo]:
    """
    VM linhas 198..203: libera quando a evolução física passa do gatilho,
    desembolsa na proporção da obra do mês, capitaliza juros e amortiza em
    N parcelas iguais após o limite ser esgotado.
    """
    total_obra = sum(abs(v) for v in obra_mes.values()) or 1.0
    evolucao_acum, liberado, saldo = 0.0, 0.0, 0.0
    capt, juros, amort = {}, {}, {}
    parcela = p.financiamento_limite / p.financiamento_prazo_amort
    amortizado = 0.0

    for m in meses:
        ev = abs(obra_mes[m]) / total_obra
        evolucao_acum += ev
        c = 0.0
        if liberado < p.financiamento_limite and evolucao_acum >= p.financiamento_gatilho_obra:
            c = min(p.financiamento_limite * ev, p.financiamento_limite - liberado)
            liberado += c
        j = saldo * p.juros_mensal
        a = 0.0
        if liberado >= p.financiamento_limite and amortizado < p.financiamento_limite:
            a = min(parcela, p.financiamento_limite - amortizado)
            amortizado += a
        saldo += c + j - a
        capt[m], juros[m], amort[m] = c, -j, -a

    return [
        LinhaFluxo("FIN_CAPT", "(+) Receitas c/ financiamento", "FINANCEIRO", capt),
        LinhaFluxo("FIN_AMORT", "(-) Amortização de financiamento", "FINANCEIRO", amort),
        LinhaFluxo("FIN_JUROS", "(-) Juros s/ financiamento", "FINANCEIRO", juros),
    ]


# ==========================================================================
# 4. INDICADORES   (SIMULAÇÕES linhas 57..67)
# ==========================================================================

def vpl(fluxos: list[float], taxa: float) -> float:
    """Σ FC_n / (1+i)^n — n contado a partir de 1, como o PV() da planilha."""
    return sum(fc / (1 + taxa) ** (n + 1) for n, fc in enumerate(fluxos))


def tir(fluxos: list[float], chute: float = 0.1) -> Optional[float]:
    """Newton-Raphson com bisseção de reserva (equivale ao IRR do Excel)."""
    def f(r):
        return sum(fc / (1 + r) ** n for n, fc in enumerate(fluxos))

    # varre o intervalo procurando troca de sinal antes de bissectar
    grade = [-0.95 + i * 0.01 for i in range(1, 1000)]
    lo = hi = None
    ant_r, ant_v = grade[0], f(grade[0])
    for r in grade[1:]:
        v = f(r)
        if ant_v * v < 0:
            lo, hi = ant_r, r
            break
        ant_r, ant_v = r, v
    if lo is None:
        return None
    flo, fhi = f(lo), f(hi)
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def mtir(fluxos: list[float], taxa_financiamento: float, taxa_reinvestimento: float
         ) -> Optional[float]:
    """
    TIR Modificada (MIRR). Recomendada no lugar da TIR simples porque o fluxo de
    uma incorporação troca de sinal várias vezes (obra × recebimento pós-chaves)
    e a TIR pode não existir ou ter múltiplas raízes — que é exatamente o que
    acontece com esta SPE.
    """
    n = len(fluxos) - 1
    if n <= 0:
        return None
    vp_neg = sum(fc / (1 + taxa_financiamento) ** i
                 for i, fc in enumerate(fluxos) if fc < 0)
    vf_pos = sum(fc * (1 + taxa_reinvestimento) ** (n - i)
                 for i, fc in enumerate(fluxos) if fc > 0)
    if vp_neg == 0 or vf_pos <= 0:
        return None
    return (vf_pos / -vp_neg) ** (1 / n) - 1


@dataclass
class Indicadores:
    vgv: float
    receita_liquida: float
    lucro: float
    margem: float
    custo_m2_privativa: float
    preco_m2_vgv: float
    eficiencia: float
    exposicao_maxima: float       # SIMULAÇÕES L66 — pior saldo de caixa
    mes_exposicao: Optional[date]
    vpl: float                    # L64
    tir_anual: Optional[float]    # L65 — None quando o fluxo troca de sinal várias vezes
    mtir_anual: Optional[float]   # alternativa robusta à TIR
    aporte_necessario: float      # L63


def calcular_indicadores(dre: DRE, obra: Obra, fluxo: FluxoMensal,
                         p: Premissas, area_privativa: float) -> Indicadores:
    saldo = fluxo.saldo_caixa()
    pior = min(saldo.values()) if saldo else 0.0
    mes_pior = min(saldo, key=saldo.get) if saldo else None

    ll = fluxo.lucro_liquido()
    serie = [ll[m] for m in fluxo.meses]

    # TIR anual sobre o fluxo agrupado por ano (SIMULAÇÕES M57:M65)
    por_ano: dict[int, float] = {}
    for m in fluxo.meses:
        por_ano[m.year] = por_ano.get(m.year, 0.0) + ll[m]
    serie_anual = [por_ano[a] for a in sorted(por_ano)]
    t = tir(serie_anual)
    mt = mtir(serie_anual, p.tma_anual, p.tma_anual)

    return Indicadores(
        vgv=dre.vgv,
        receita_liquida=dre.receita_liquida,
        lucro=dre.lucro,
        margem=dre.margem,
        custo_m2_privativa=obra.custo_m2_privativa,
        preco_m2_vgv=dre.vgv / area_privativa if area_privativa else 0.0,
        eficiencia=obra.eficiencia,
        exposicao_maxima=min(pior, 0.0),
        mes_exposicao=mes_pior if pior < 0 else None,
        vpl=vpl(serie, p.tma_mensal),
        tir_anual=t,
        mtir_anual=mt,
        aporte_necessario=abs(min(pior, 0.0)),
    )


# ==========================================================================
# 5. ORÇADO × ATUALIZADO × REALIZADO   (aba VIABILIDADE)
# ==========================================================================

@dataclass
class ComparativoLinha:
    conta: str
    orcado: float          # coluna N — a viabilidade aprovada, congelada
    atualizado: float      # coluna Q — a viabilidade recalculada hoje
    realizado: float       # coluna V — caixa efetivo (Sienge)

    @property
    def a_realizar(self) -> float:      # coluna Y
        return self.atualizado - self.realizado

    @property
    def variacao(self) -> float:        # coluna S = Q/N − 1
        return (self.atualizado / self.orcado - 1) if self.orcado else 0.0

    @property
    def desvio(self) -> float:          # coluna U = N − Q
        return self.orcado - self.atualizado


def comparativo(orcado: DRE, atualizado: DRE,
                realizado_por_conta: dict[str, float]) -> list[ComparativoLinha]:
    """Monta a visão da aba VIABILIDADE: orçado × atualizado × realizado."""
    contas = [
        ("VGV", "vgv"), ("(-) Comissão s/ vendas", "comissao"),
        ("RECEITA C/ VENDAS SPE", "receita_spe"), ("(-) Impostos", "ret"),
        ("(-) Distratos", "distratos"), ("(-) Despesas comerciais", "despesas_comerciais"),
        ("RECEITA LÍQUIDA", "receita_liquida"),
        ("(-) Terreno - Permuta", "terreno_permuta"),
        ("(-) Terreno - Pagamento", "terreno_pagamento"),
        ("(-) Terreno - Outros", "terreno_registro"),
        ("(-) Obra - Custo Raso", "obra_custo_raso"),
        ("(-) Taxa de Administração - Obra", "taxa_adm_obra"),
        ("(-) Taxa de Administração - Carteira", "taxa_viabilizacao"),
        ("(-) Incorporação - Decoração", "decoracao"),
        ("(-) Incorporação - Outros", "projetos_e_outros"),
        ("(-) Marketing - Stand", "marketing_stand"),
        ("(-) Marketing - Propaganda", "marketing_propaganda"),
        ("(-) Outras despesas administrativas", "outras_desp_adm"),
        ("(+) Outras receitas administrativas", "outras_entradas"),
    ]
    return [ComparativoLinha(rotulo, getattr(orcado, campo), getattr(atualizado, campo),
                             realizado_por_conta.get(campo, 0.0))
            for rotulo, campo in contas]
