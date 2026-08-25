"""
O que é cada dado do estudo de viabilidade.

Este arquivo é a **fonte única** dessa explicação. A tela `/guia` monta o
manual a partir dele, e os formulários de cadastro puxam daqui o texto do "?"
que fica ao lado de cada campo. Escrever a explicação em dois lugares seria
garantir que um dos dois envelhece.

Cada verbete responde sempre às mesmas quatro perguntas, porque são as quatro
que aparecem quando alguém senta para preencher:

    o_que      o que esse número significa em português
    de_onde    quem produz o dado: Sienge, planilha de custo, decisão da
               diretoria, contrato, ou o próprio motor
    como       como se preenche ou como o sistema calcula
    cuidado    o erro que essa linha costuma esconder (opcional)

`unidade` diz em que grandeza o número é digitado — é o que evita o erro mais
comum de todos, que é digitar 10 quando o campo quer 0,10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Verbete:
    chave: str
    titulo: str
    unidade: str = ""
    o_que: str = ""
    de_onde: str = ""
    como: str = ""
    cuidado: str = ""
    exemplo: str = ""


@dataclass
class Secao:
    slug: str
    titulo: str
    resumo: str
    verbetes: list = field(default_factory=list)


# =====================================================================
# 1. O empreendimento
# =====================================================================
CADASTRO = Secao("cadastro", "O empreendimento", """
Identifica a SPE e delimita o que entra na conta. São poucos campos, mas dois
deles — a área privativa e o mês de corte — mudam praticamente todos os
indicadores da tela.""", [
    Verbete("nome", "Nome do empreendimento",
        o_que="Como a SPE aparece nas listas e nos relatórios.",
        de_onde="Você escolhe. O usual é repetir o nome do Sienge para não haver dois nomes para a mesma obra.",
        como="Nome comercial, sem o CNPJ. Exemplo: “SPE KIEV — Doca Sede”.",
        exemplo="SPE KIEV — Doca Sede"),

    Verbete("sienge_enterprise_id", "Código do empreendimento no Sienge", "número",
        o_que="O id que o Sienge usa para essa obra. É por ele que a importação sabe quais lançamentos são dessa SPE.",
        de_onde="Sienge, no cadastro do empreendimento.",
        como="Só é obrigatório se você for importar do Sienge. Sem ele o sistema funciona, mas você digita tudo à mão.",
        exemplo="26003"),

    Verbete("area_privativa", "Área privativa total", "m²",
        o_que="A soma da área privativa de todas as unidades que entram no VGV. É a base de quase tudo: preço por m², custo raso por m² e eficiência.",
        de_onde="Quadro de áreas do projeto, conferido contra o cadastro de unidades do Sienge.",
        como="Some a área privativa das unidades que serão vendidas. Não inclua área comum, garagem avulsa que não é vendida separada, nem unidade permutada que sai do VGV.",
        cuidado="Esse foi o número que mais deu trabalho na migração da Kiev. O cadastro do Sienge e a lista que a planilha usava para o VGV não eram a mesma lista — duas salas existiam só no Sienge e um rooftop existia só na planilha. Se a área não bate com a soma das unidades, o sistema avisa, e o certo é resolver a diferença, não escolher um dos dois números.",
        exemplo="10.731,06"),

    Verbete("area_construida", "Área construída total", "m²",
        o_que="Toda a área do projeto, incluindo comum e garagem. Serve para calcular a eficiência (privativa ÷ construída).",
        de_onde="Quadro de áreas do projeto.",
        como="Área total do memorial. Não é usada no resultado, só nos indicadores de projeto.",
        exemplo="22.909,46"),

    Verbete("data_lancamento", "Data de lançamento", "data",
        o_que="Quando a venda começou. Marca o início da linha do tempo do estudo.",
        de_onde="Decisão comercial.",
        como="Mês em que a primeira unidade pôde ser vendida."),

    Verbete("data_entrega_prevista", "Entrega prevista (chaves)", "data",
        o_que="Quando o cliente recebe a chave. É a data que separa duas coisas muito diferentes: até aqui o cliente paga parcela de tabela; a partir daqui entra o repasse bancário e começa o pós-chaves.",
        de_onde="Cronograma de obra.",
        como="Mês previsto de entrega no habite-se.",
        cuidado="Antecipar ou atrasar essa data desloca todo o bloco de chaves — que costuma ser 20% do VGV — de uma vez. É a premissa que mais mexe na exposição de caixa."),

    Verbete("mes_corte_realizado", "Mês de corte do realizado", "data",
        o_que="A fronteira entre o que já aconteceu e o que ainda é projeção. Movimento até esta data conta como realizado; a partir do mês seguinte, o motor projeta.",
        de_onde="Você escolhe, normalmente o último mês fechado da contabilidade.",
        como="Último dia do mês fechado. Se a contabilidade fechou julho, use 31/07.",
        cuidado="Contrato assinado depois do corte não deve trazer a comissão dele para dentro do realizado. A planilha somava a comissão de todo contrato assinado, inclusive os posteriores ao fechamento — aqui o corte é respeitado, e é por isso que o número pode divergir da planilha antiga.",
        exemplo="31/07/2026"),
])


# =====================================================================
# 2. Preço e tabela de venda
# =====================================================================
TABELA = Secao("tabela", "Preço e tabela de venda", """
A tabela de venda é a receita da SPE dividida no tempo. Ela responde a uma
pergunta só: de cada R$ 100 que o cliente assina, quanto entra no ato, quanto
entra mensalmente até as chaves, quanto entra em balões e quanto entra no
repasse. A soma tem de ser exatamente 100% — o banco recusa o cadastro se não
for, porque uma tabela que soma 97% inventa um desconto invisível de 3%.""", [
    Verbete("preco_m2_estoque", "Preço do estoque", "R$/m²",
        o_que="Por quanto você assume que as unidades ainda não vendidas serão vendidas. É a premissa que mais separa um cenário do outro.",
        de_onde="Decisão comercial, apoiada na tabela vigente e no que o mercado da região está praticando.",
        como="Preço bruto por metro quadrado privativo — antes de descontar a comissão. O cenário realista costuma usar o preço de tabela já cadastrado unidade a unidade; o otimista e o pessimista usam este número aplicado à área.",
        exemplo="18.228,66"),

    Verbete("comissao", "Comissão sobre vendas", "% do bruto",
        o_que="O que fica com quem vendeu. Sai de dentro do preço: o cliente paga 100, a corretora fica com a comissão e a SPE recebe o resto.",
        de_onde="Contrato com a imobiliária.",
        como="Percentual sobre o valor bruto. Digite 0,06 para 6%.",
        cuidado="Se o preço bruto de uma unidade foi digitado à mão e não bate com o percentual, o sistema respeita o preço digitado e recalcula a comissão daquela unidade. Foi assim que a unidade 2205 da Kiev apareceu com 5,29% em vez de 6%.",
        exemplo="0,06"),

    Verbete("ato", "Ato", "% do bruto",
        o_que="A entrada, paga na assinatura do contrato.",
        de_onde="Tabela de venda aprovada.",
        como="Percentual do valor bruto. Digite 0,04 para 4%.",
        exemplo="0,04"),

    Verbete("mensais", "Mensais", "% do bruto",
        o_que="O bloco pago em parcelas mensais entre a assinatura e as chaves. É o que sustenta o caixa durante a obra.",
        de_onde="Tabela de venda aprovada.",
        como="Percentual total do bloco, não o valor de cada parcela. O número de parcelas é o campo seguinte.",
        exemplo="0,35"),

    Verbete("n_mensais", "Número de parcelas mensais", "meses",
        o_que="Em quantos meses o bloco mensal se dilui.",
        de_onde="Tabela de venda aprovada.",
        como="Normalmente é o prazo entre a venda e as chaves. Quanto mais parcelas, menor a entrada de caixa por mês e maior a exposição.",
        exemplo="60"),

    Verbete("semestrais", "Semestrais (balões)", "% do bruto",
        o_que="Reforços semestrais, os “balões” da tabela.",
        de_onde="Tabela de venda aprovada.",
        como="Percentual total do bloco. O motor distribui de seis em seis meses.",
        exemplo="0,35"),

    Verbete("anuais", "Anuais", "% do bruto",
        o_que="Reforços anuais, quando a tabela usa esse formato em vez de semestrais.",
        de_onde="Tabela de venda aprovada.",
        como="Percentual total do bloco. Deixe zero se a tabela não tem reforço anual.",
        exemplo="0,00"),

    Verbete("unica", "Parcela única", "% do bruto",
        o_que="Um pagamento avulso, em data marcada, que não se encaixa em nenhum dos blocos anteriores.",
        de_onde="Tabela de venda aprovada.",
        como="Deixe zero se não existir.",
        exemplo="0,00"),

    Verbete("chaves", "Chaves (repasse)", "% do bruto",
        o_que="O saldo pago na entrega, quase sempre via financiamento bancário do cliente. É a maior entrada isolada do estudo.",
        de_onde="Tabela de venda aprovada.",
        como="Percentual do valor bruto.",
        cuidado="Entre o fim dos desembolsos de obra e a chegada do repasse existe um vale de caixa. É esse vale que produz a exposição máxima — e é ele que faz a TIR não existir em muitos estudos, porque o fluxo troca de sinal mais de uma vez.",
        exemplo="0,20"),

    Verbete("meses_pos_chaves", "Meses de pós-chaves", "meses",
        o_que="Em quantos meses o saldo que não foi repassado na entrega se dilui depois das chaves.",
        de_onde="Política de crédito da incorporadora.",
        como="Número de meses após a entrega. Serve para o caso em que parte do repasse atrasa.",
        exemplo="6"),

    Verbete("preco_investidor_unidade", "Preço da unidade de investidor", "R$",
        o_que="Preço fechado de unidades vendidas em bloco para investidor, que não seguem a tabela normal.",
        de_onde="Contrato específico.",
        como="Valor por unidade. Deixe zero se não houver venda de investidor no plano."),
])


# =====================================================================
# 3. Plano de vendas
# =====================================================================
PLANO = Secao("plano", "Plano de vendas", """
Quantas unidades você assume vender em cada mês. É a premissa que o mercado
controla e você não — e por isso é a que mais merece ter três versões
diferentes, uma por cenário.""", [
    Verbete("plano_mes", "Unidades vendidas no mês", "unidades",
        o_que="Quantas unidades do estoque saem naquele mês. O motor pega esse número, aplica a tabela de venda e gera as parcelas de cada coorte.",
        de_onde="Decisão comercial, normalmente calibrada pela velocidade de vendas já observada.",
        como="Uma linha por mês, do lançamento até esgotar o estoque. A soma das unidades do plano precisa fechar com o estoque disponível — se sobrar unidade sem mês de venda, ela nunca entra no fluxo.",
        cuidado="Vender mais rápido melhora o caixa, mas não melhora o lucro na mesma proporção: o preço é o mesmo e a comissão também. Se o cenário otimista mostra lucro muito maior só por causa da velocidade, provavelmente o preço também foi mexido junto."),

    Verbete("tipo_venda", "Tipo da venda", "normal ou investidor",
        o_que="Distingue a venda de tabela da venda em bloco para investidor, que tem preço próprio.",
        de_onde="Decisão comercial.",
        como="Use “investidor” só nas unidades que têm preço fechado diferente da tabela."),
])


# =====================================================================
# 4. Obra
# =====================================================================
OBRA = Secao("obra", "Obra", """
Duas coisas separadas que costumam ser confundidas: **quanto** custa a obra e
**quando** esse custo é desembolsado. O total vem do orçamento; a distribuição
no tempo vem da curva física. As duas precisam pertencer ao mesmo orçamento —
usar o custo de uma versão com a curva de outra é um erro que passa
despercebido porque o total continua certo.""", [
    Verbete("custo_raso", "Custo raso da obra", "R$",
        o_que="Todo o custo de construir, sem taxa de administração, sem terreno e sem despesa de incorporação. É a maior saída do estudo.",
        de_onde="Orçamento da obra, na planilha de custo.",
        como="Valor total positivo — o sistema aplica o sinal. Se você tem o custo por metro quadrado, multiplique pela área privativa.",
        cuidado="Na planilha antiga, a linha de desembolso da projeção era um conjunto de valores colados, desligado do cronograma: mexer no cronograma não mudava o fluxo. Aqui o desembolso é sempre a curva aplicada ao custo, então os dois andam juntos.",
        exemplo="104.048.246,00"),

    Verbete("custo_m2", "Custo raso por m² privativo", "R$/m²",
        o_que="O custo raso dividido pela área privativa. É o número que se compara entre empreendimentos e com o mercado.",
        de_onde="Calculado pelo sistema.",
        como="Não se digita — sai de custo raso ÷ área privativa.",
        exemplo="9.696"),

    Verbete("curva_fisica", "Curva física da obra", "% por mês",
        o_que="Quanto por cento da obra é executado em cada mês. É ela que espalha o custo raso no tempo.",
        de_onde="Cronograma físico-financeiro da obra.",
        como="Um percentual por mês, do início ao fim da obra. A soma de cada atividade tem de dar exatamente 100% — o banco recusa o cadastro se não der.",
        cuidado="Curva errada não muda o lucro, muda o caixa. O lucro do estudo continua igual e a exposição máxima muda de milhões — é o tipo de erro que só aparece quando falta dinheiro."),

    Verbete("taxa_adm_obra", "Taxa de administração — obra", "% do custo raso",
        o_que="O que a construtora cobra para administrar a obra.",
        de_onde="Contrato de administração.",
        como="Percentual sobre o custo raso. Digite 0,10 para 10%.",
        exemplo="0,10"),

    Verbete("outras_desp_adm_perc", "Outras despesas administrativas", "% do custo raso",
        o_que="Despesas administrativas menores, tratadas como um percentual do custo raso em vez de item a item.",
        de_onde="Histórico da incorporadora.",
        como="Percentual sobre o custo raso. Digite 0,015 para 1,5%.",
        exemplo="0,015"),
])


# =====================================================================
# 5. Terreno
# =====================================================================
TERRENO = Secao("terreno", "Terreno", """
O terreno entra de duas formas muito diferentes, e a diferença importa:
a **permuta** é custo do estudo mas não é saída de caixa, porque se paga em
unidades; o **pagamento em dinheiro** é as duas coisas. Confundir os dois faz
o lucro fechar e o caixa não.""", [
    Verbete("terreno_permuta", "Terreno — permuta", "R$",
        o_que="O valor das unidades entregues ao proprietário do terreno como pagamento.",
        de_onde="Contrato de permuta, avaliado pelo preço de tabela das unidades permutadas.",
        como="Não se digita direto: o sistema soma as unidades marcadas como permuta no cadastro.",
        cuidado="Entra no resultado e **não** entra no fluxo de caixa. É uma das razões pelas quais somar as colunas mensais nunca vai dar a linha do DRE."),

    Verbete("terreno_parcelas", "Terreno — parcelas de pagamento", "R$ por parcela",
        o_que="A parte do terreno paga em dinheiro, parcela a parcela.",
        de_onde="Contrato de compra do terreno.",
        como="Uma linha por parcela, com valor e mês. A primeira costuma ser a maior, na assinatura.",
        exemplo="2.200.000 · 350.000 · 350.000 · 300.000 · 250.000"),

    Verbete("terreno_registro_perc", "Terreno — registro e outros", "% do pagamento",
        o_que="ITBI, cartório e despesas de transmissão.",
        de_onde="Prática do município.",
        como="Percentual sobre a parte paga em dinheiro. Digite 0,025 para 2,5%.",
        exemplo="0,025"),
])


# =====================================================================
# 6. Deduções da receita
# =====================================================================
DEDUCOES = Secao("deducoes", "Deduções da receita", """
O que sai da receita antes de chegar na receita líquida. São poucos itens, mas
o RET incide sobre tudo que a SPE recebe, então um ponto percentual aqui vale
milhões no estudo inteiro.""", [
    Verbete("ret", "Impostos (RET)", "% da receita SPE",
        o_que="O Regime Especial de Tributação do patrimônio de afetação, que substitui IRPJ, CSLL, PIS e Cofins por uma alíquota única sobre a receita.",
        de_onde="Legislação. Hoje 4% para o RET geral e 1% no programa habitacional; a MMI vem usando 4,5% incluindo o que não é RET puro.",
        como="Percentual sobre a receita da SPE. Digite 0,045 para 4,5%.",
        cuidado="É calculado sobre o que a SPE **recebe**, não sobre o que ela fatura. Por isso o imposto do estudo acompanha o caixa e não o contrato.",
        exemplo="0,045"),

    Verbete("distratos", "Distratos", "% da receita SPE",
        o_que="A parcela da receita que você assume que vai voltar atrás — contrato desfeito, unidade devolvida ao estoque.",
        de_onde="Histórico da incorporadora.",
        como="Percentual sobre a receita. Zero significa assumir que nenhum contrato será desfeito, o que é uma premissa otimista e vale deixar explícita.",
        exemplo="0,00"),

    Verbete("despesas_comerciais", "Despesas comerciais", "R$",
        o_que="Gastos de vender que não são comissão: material, evento, corretagem de apoio.",
        de_onde="Orçamento comercial.",
        como="Valor fixo total, não percentual.",
        exemplo="30.750,00"),

    Verbete("taxa_viabilizacao", "Taxa de administração — carteira", "% da receita SPE",
        o_que="O que a incorporadora cobra da SPE para administrar a carteira de recebíveis.",
        de_onde="Contrato de administração.",
        como="Percentual sobre a receita da SPE. Digite 0,05 para 5%.",
        exemplo="0,05"),
])


# =====================================================================
# 7. Incorporação e marketing
# =====================================================================
INCORPORACAO = Secao("incorporacao", "Incorporação e marketing", """
Despesas de valor fixo, decididas no lançamento. São as mais fáceis de
preencher e as mais fáceis de esquecer de atualizar.""", [
    Verbete("decoracao", "Decoração", "R$",
        o_que="Apartamento decorado e ambientação do stand.",
        de_onde="Orçamento de marketing.", como="Valor total.",
        exemplo="1.545.045,14"),
    Verbete("projetos_e_outros", "Incorporação — projetos e outros", "R$",
        o_que="Projetos, aprovações, taxas, assessorias — o custo de viabilizar a incorporação.",
        de_onde="Orçamento de incorporação.", como="Valor total.",
        exemplo="2.899.156,99"),
    Verbete("marketing_stand", "Marketing — stand", "R$",
        o_que="Construção e operação do stand de vendas.",
        de_onde="Orçamento de marketing.", como="Valor total; zero se a venda usa estrutura existente.",
        exemplo="0,00"),
    Verbete("marketing_propaganda", "Marketing — propaganda", "R$",
        o_que="Mídia, agência e campanha.",
        de_onde="Orçamento de marketing.", como="Valor total.",
        exemplo="1.545.045,14"),
    Verbete("outras_entradas", "Outras receitas administrativas", "R$",
        o_que="Entradas que não vêm de venda de unidade: rendimento de aplicação, recuperação de despesa.",
        de_onde="Contabilidade.", como="Valor total positivo.",
        exemplo="117,95"),
])


# =====================================================================
# 8. Correção monetária
# =====================================================================
CORRECAO = Secao("correcao", "Correção monetária", """
A planilha projetava tudo em valores nominais — como se o INCC fosse zero até
2031. O sistema mantém esse comportamento por padrão, para os números baterem
com o histórico, e deixa a correção como uma escolha explícita.

Vale saber o que acontece quando se liga: na Kiev, com INCC dos dois lados, a
exposição máxima **piora** de R$ 60,2 milhões para R$ 65,6 milhões. A projeção
nominal subestimava o aporte necessário em R$ 5,4 milhões.""", [
    Verbete("indice_ate_chaves", "Índice até as chaves", "INCC-DI, IGP-M, IPCA ou nenhum",
        o_que="O índice que reajusta a parcela do cliente durante a obra.",
        de_onde="Cláusula de reajuste do contrato de venda.",
        como="Quase sempre INCC-DI. Deixe vazio para projetar em valores nominais, como a planilha fazia.",
        exemplo="INCC-DI"),

    Verbete("indice_apos_chaves", "Índice depois das chaves", "IGP-M, IPCA ou nenhum",
        o_que="O índice que reajusta o saldo depois da entrega, no período de repasse.",
        de_onde="Cláusula de reajuste do contrato de venda.",
        como="Costuma mudar de INCC para IGP-M ou IPCA na entrega.",
        exemplo="IGP-M"),

    Verbete("indice_projetado_aa", "Índice projetado", "% ao ano",
        o_que="A taxa usada nos meses que ainda não têm índice publicado.",
        de_onde="Projeção de mercado. O acumulado de 12 meses do índice é um ponto de partida honesto.",
        como="Digite 0,065 para 6,5% ao ano. O sistema usa a série histórica onde ela existe e só cai nesta taxa para o futuro.",
        exemplo="0,065"),

    Verbete("corrigir_custo_obra", "Corrigir também o custo da obra", "sim ou não",
        o_que="Se o INCC também encarece a obra, e não só a parcela do cliente.",
        de_onde="Realidade.",
        como="Deixe ligado. Corrigir só a carteira e deixar a obra em moeda de hoje inventa lucro: o mesmo INCC que reajusta a parcela do cliente é o que encarece o concreto.",
        cuidado="Este é o campo mais perigoso da tela. Desligar melhora todos os indicadores e não melhora nada na vida real."),
])


# =====================================================================
# 9. Indicadores
# =====================================================================
INDICADORES = Secao("indicadores", "Como ler os indicadores", """
Estes números o sistema calcula — você não preenche nenhum. Estão aqui porque
lê-los errado custa mais caro do que preenchê-los errado.""", [
    Verbete("vgv", "VGV", "R$",
        o_que="Valor Geral de Vendas: a soma do preço bruto de todas as unidades, vendidas e em estoque, incluindo as de permuta.",
        de_onde="Calculado: unidades vendidas pelo contrato, estoque pelo preço do cenário.",
        como="Não se digita.",
        cuidado="É valor bruto, com comissão dentro. A SPE nunca recebe o VGV."),

    Verbete("receita_liquida", "Receita líquida", "R$",
        o_que="O que sobra depois de tirar comissão, impostos, distratos e despesas comerciais. É a base sobre a qual todos os percentuais da tela são calculados.",
        de_onde="Calculado.", como="Não se digita."),

    Verbete("lucro", "Lucro", "R$",
        o_que="Receita líquida menos terreno, obra, taxas de administração, incorporação e marketing.",
        de_onde="Calculado.", como="Não se digita.",
        cuidado="É lucro do estudo inteiro, do lançamento à última parcela — não é lucro anual e não desconta imposto de renda do sócio."),

    Verbete("margem", "Margem", "%",
        o_que="Lucro ÷ receita líquida.",
        de_onde="Calculado.", como="Não se digita.",
        cuidado="Margem sobre receita líquida, não sobre VGV. A mesma obra tem uma margem menor se calculada sobre o VGV — ao comparar com outra incorporadora, confirme qual base ela usou."),

    Verbete("exposicao_maxima", "Exposição máxima", "R$",
        o_que="O momento em que a SPE está mais no vermelho: o pior saldo acumulado de caixa do estudo inteiro.",
        de_onde="Calculado a partir do fluxo mensal.",
        como="Não se digita.",
        cuidado="É o número que diz quanto dinheiro o sócio precisa ter disponível, e em que mês. Um estudo com lucro alto e exposição alta pode simplesmente não ser executável."),

    Verbete("aporte_necessario", "Aporte necessário", "R$",
        o_que="Quanto precisa entrar de capital próprio para o caixa nunca ficar negativo.",
        de_onde="Calculado.", como="Não se digita."),

    Verbete("vpl", "VPL", "R$",
        o_que="O fluxo de caixa trazido a valor presente pela TMA. Positivo significa que o projeto rende mais do que a taxa mínima que você exige.",
        de_onde="Calculado.", como="Não se digita."),

    Verbete("tma_anual", "TMA", "% ao ano",
        o_que="Taxa Mínima de Atratividade: o retorno abaixo do qual não vale a pena fazer a obra, porque o dinheiro renderia mais em outro lugar.",
        de_onde="Decisão da diretoria.",
        como="Digite 0,18 para 18% ao ano. É o único campo desta seção que você preenche.",
        exemplo="0,18"),

    Verbete("tir", "TIR", "% ao ano",
        o_que="A taxa que zera o VPL.",
        de_onde="Calculado — quando existe.",
        como="Não se digita.",
        cuidado="Aparece como “—” quando o fluxo troca de sinal mais de uma vez e não existe raiz real, que é o caso da maioria dos estudos de incorporação, por causa do vale entre o fim da obra e o repasse. A planilha antiga mostrava uma TIR alta porque a série tinha valores deslocados à mão. Quando a TIR não existe, use a MTIR."),

    Verbete("mtir", "MTIR", "% ao ano",
        o_que="TIR modificada: reinveste os excedentes à TMA em vez de à própria taxa interna. Existe sempre e é comparável entre projetos.",
        de_onde="Calculado.", como="Não se digita."),
])


# =====================================================================
# 10. As colunas da tela de viabilidade
# =====================================================================
COLUNAS = Secao("colunas", "As quatro colunas da viabilidade", """
A tela mostra a mesma conta quatro vezes, em quatro momentos diferentes. A
planilha misturava as quatro na mesma linha e funcionava enquanto uma pessoa
soubesse onde ficava a fronteira. Aqui a fronteira é o mês de corte.""", [
    Verbete("col_orcado", "Orçado",
        o_que="O estudo congelado no lançamento. Não muda mais, nunca.",
        de_onde="A rodada marcada como orçada, gravada uma vez.",
        como="O sistema se recusa a recalcular esta coluna."),
    Verbete("col_atualizado", "Atualizado",
        o_que="O mesmo estudo com as premissas de hoje: preço de hoje, custo de hoje, velocidade de hoje.",
        de_onde="A última rodada do cenário principal.",
        como="Muda toda vez que você clica em Recalcular."),
    Verbete("col_realizado", "Realizado",
        o_que="O que já passou pelo caixa até o mês de corte, vindo dos lançamentos do Sienge.",
        de_onde="Movimentos importados, classificados por conta.",
        como="Não se digita — vem da importação.",
        cuidado="Lançamento sem conta cai em “A CLASSIFICAR” e não aparece em nenhuma linha do resultado. A tela avisa quando isso acontece; ignorar o aviso é ler um realizado menor do que o verdadeiro."),
    Verbete("col_a_realizar", "A realizar",
        o_que="Atualizado menos realizado: o que ainda falta acontecer.",
        de_onde="Calculado.", como="Não se digita."),
    Verbete("col_variacao", "Variação",
        o_que="Atualizado ÷ orçado − 1. Quanto o estudo de hoje se afastou do que foi aprovado no lançamento.",
        de_onde="Calculado.", como="Não se digita.",
        cuidado="Variação positiva numa linha de despesa é má notícia; numa linha de receita é boa. O sinal do número não diz sozinho se é bom ou ruim."),
])


SECOES = [CADASTRO, TABELA, PLANO, OBRA, TERRENO, DEDUCOES, INCORPORACAO,
          CORRECAO, INDICADORES, COLUNAS]

# índice plano, para o "?" ao lado de um campo achar seu verbete pela chave
POR_CHAVE = {v.chave: v for s in SECOES for v in s.verbetes}


def ajuda(chave: str) -> Optional[Verbete]:
    return POR_CHAVE.get(chave)


# =====================================================================
# De cada linha do DRE para o verbete que a explica
# =====================================================================
# A tela de viabilidade usa isto para pendurar um "?" ao lado do nome da conta.
# Quando não há verbete próprio, o texto curto abaixo já resolve.
LINHA_AJUDA = {
    "VGV": "vgv",
    "(-) Comissão s/ vendas": "comissao",
    "(-) Impostos": "ret",
    "(-) Distratos": "distratos",
    "(-) Despesas comerciais": "despesas_comerciais",
    "RECEITA LÍQUIDA": "receita_liquida",
    "(-) Terreno - Permuta": "terreno_permuta",
    "(-) Terreno - Pagamento": "terreno_parcelas",
    "(-) Terreno - Outros": "terreno_registro_perc",
    "(-) Obra - Custo Raso": "custo_raso",
    "(-) Taxa de Administração - Obra": "taxa_adm_obra",
    "(-) Taxa de Administração - Carteira": "taxa_viabilizacao",
    "(-) Incorporação - Decoração": "decoracao",
    "(-) Incorporação - Outros": "projetos_e_outros",
    "(-) Marketing - Stand": "marketing_stand",
    "(-) Marketing - Propaganda": "marketing_propaganda",
    "(-) Outras despesas administrativas": "outras_desp_adm_perc",
    "(+) Outras receitas administrativas": "outras_entradas",
    "LUCRO": "lucro",
}

LINHA_TEXTO = {
    "RECEITA C/ VENDAS SPE":
        "O que sobra do VGV depois de tirar a comissão. É o que a SPE "
        "efetivamente recebe dos clientes, e a base sobre a qual o RET e a "
        "taxa de carteira incidem.",
    "(+) Correção monetária da carteira":
        "O quanto o índice de reajuste acrescenta às parcelas ainda não pagas. "
        "Só aparece quando o cenário tem índice configurado.",
    "(-) Correção monetária da obra":
        "O mesmo índice encarecendo o custo que falta desembolsar. Anda junto "
        "com a linha de cima de propósito.",
}


def ajuda_da_linha(rotulo: str):
    """Verbete de uma linha do DRE, ou um texto curto, ou nada."""
    chave = LINHA_AJUDA.get(rotulo)
    if chave:
        return POR_CHAVE.get(chave)
    texto = LINHA_TEXTO.get(rotulo)
    if texto:
        return Verbete(chave=rotulo, titulo=rotulo, o_que=texto)
    return None
