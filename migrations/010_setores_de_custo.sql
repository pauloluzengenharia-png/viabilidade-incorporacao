-- =====================================================================
-- 010 — cada setor de custo com composição própria
--
-- Até aqui, um custo de incorporação era um número solto numa premissa:
-- `decoracao = 1545045.14`. Funciona para reproduzir a planilha, e é péssimo
-- para orçar: ninguém sabe do que aquele número é feito, e revisá-lo é digitar
-- outro número solto por cima.
--
-- Agora cada setor tem a sua composição — itens com descrição, quantidade,
-- unidade e valor unitário — e a linha do resultado recebe a **soma** dessa
-- composição. É a diferença entre "decoração custa 1,5 milhão" e "decoração é
-- mobiliário do decorado, projeto de interiores, enxoval e paisagismo, e cada
-- um custa isto".
--
-- A conversão é feita sem mexer em total nenhum: cada premissa de valor fixo
-- que existe hoje vira uma composição de um item só, com o mesmo valor e a
-- descrição dizendo de onde veio. Os 130 testes contra a planilha continuam
-- passando — se não passassem, esta migration estaria errada.
--
-- E entram dois setores que faltavam e que o estudo pagava sem enxergar:
-- regularização fundiária e legalização do empreendimento.
-- =====================================================================

CREATE TABLE setor_custo (
    codigo      text PRIMARY KEY,
    nome        text NOT NULL,
    linha_dre   text NOT NULL,          -- onde a soma entra no resultado
    bloco       text NOT NULL,          -- agrupamento na tela de custos
    desembolso  text NOT NULL DEFAULT 'obra'
                CHECK (desembolso IN ('obra', 'lancamento', 'a_vista', 'entrega')),
    ordem       smallint NOT NULL DEFAULT 0,
    resumo      text                    -- o que entra neste setor, em uma frase
);

COMMENT ON COLUMN setor_custo.desembolso IS
 'Quando o dinheiro sai: obra = acompanha a curva física; lancamento = diluído
  até o lançamento; a_vista = tudo no primeiro mês; entrega = concentrado no
  fim da obra. Muda o caixa, nunca o resultado.';

CREATE TABLE composicao_item (
    id          bigserial PRIMARY KEY,
    cenario_id  bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    setor       text   NOT NULL REFERENCES setor_custo ON DELETE CASCADE,
    ordem       smallint NOT NULL DEFAULT 0,
    descricao   text   NOT NULL,
    quantidade  numeric(14,4),
    unidade     text,                   -- 'un', 'm²', 'vb', 'mês'
    valor_unitario numeric(16,4),
    valor       numeric(16,2) NOT NULL, -- o que soma; positivo
    observacao  text,
    CHECK (valor >= 0)
);

CREATE INDEX ON composicao_item (cenario_id, setor, ordem);

COMMENT ON COLUMN composicao_item.valor IS
 'O que soma na linha, sempre positivo — o motor aplica o sinal. Quando há
  quantidade e valor unitário, é o produto dos dois; quando não há, é digitado
  direto, que é o caso de verba fechada.';

-- ---------------------------------------------------------------- setores
INSERT INTO setor_custo (codigo, nome, linha_dre, bloco, desembolso, ordem, resumo) VALUES
  ('regularizacao_fundiaria', 'Regularização fundiária',
   '(-) Terreno - Regularização fundiária', 'Terreno', 'obra', 10,
   'O custo de deixar a matrícula limpa: retificação de área, georreferenciamento, desmembramento ou unificação, usucapião, baixa de ônus e certidões.'),

  ('legalizacao', 'Legalização do empreendimento',
   '(-) Incorporação - Legalização', 'Incorporação e comercial', 'obra', 20,
   'Aprovações e licenças até o habite-se: prefeitura, órgão ambiental, corpo de bombeiros, concessionárias, registro de incorporação e averbação.'),

  ('decoracao', 'Decoração',
   '(-) Incorporação - Decoração', 'Incorporação e comercial', 'entrega', 30,
   'Apartamento decorado e ambientação do stand.'),

  ('projetos_e_outros', 'Projetos e outros',
   '(-) Incorporação - Outros', 'Incorporação e comercial', 'obra', 40,
   'Projetos de arquitetura e complementares, assessorias, consultorias e taxas.'),

  ('marketing_stand', 'Marketing — stand',
   '(-) Marketing - Stand', 'Incorporação e comercial', 'lancamento', 50,
   'Construção e operação do stand de vendas.'),

  ('marketing_propaganda', 'Marketing — propaganda',
   '(-) Marketing - Propaganda', 'Incorporação e comercial', 'obra', 60,
   'Mídia, agência e campanha.'),

  ('despesas_comerciais', 'Despesas comerciais',
   '(-) Despesas comerciais', 'Deduções da receita', 'obra', 70,
   'Gastos de vender que não são comissão: material, evento, corretagem de apoio.');

-- ------------------------------------------- conversão dos valores de hoje
-- Cada premissa de valor fixo vira uma composição de um item só. O total não
-- muda; o que muda é passar a existir um lugar para detalhá-lo.
INSERT INTO composicao_item (cenario_id, setor, ordem, descricao, valor, observacao)
SELECT p.cenario_id, p.chave, 1,
       'Valor único, como veio da planilha de origem',
       abs(p.valor),
       'Convertido automaticamente na migration 010. Substitua por itens quando revisar o orçamento deste setor.'
  FROM premissa p
  JOIN setor_custo s ON s.codigo = p.chave
 WHERE p.valor <> 0;

COMMENT ON TABLE composicao_item IS
 'A composição de um setor de custo, item a item. A linha do resultado recebe a
  soma. Quando o setor não tem item nenhum, a linha vale zero — não existe mais
  valor solto escondido em premissa.';
