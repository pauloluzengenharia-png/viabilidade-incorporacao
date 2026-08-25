-- =====================================================================
-- 007 — o resultado e o fluxo são duas coisas
--
-- Erro que a primeira versão cometeu e a planilha também comete: somar as
-- colunas mensais para obter a linha do DRE. Não dá. O fluxo de caixa só
-- enxerga o que passa pela conta corrente dentro do horizonte; o DRE enxerga
-- o resultado inteiro do empreendimento — inclusive a permuta, que entra e
-- sai sem tocar em caixa, e as parcelas que caem depois do horizonte.
--
-- Somar o fluxo dava "RECEITA C/ VENDAS SPE" de R$ 157 M numa DRE cuja
-- RECEITA LÍQUIDA é R$ 179 M: as partes não fechavam com o todo.
--
-- Agora são duas tabelas: `resultado_projetado` (o DRE) e `fluxo_projetado`
-- (o caixa mês a mês). A tela de viabilidade lê a primeira; a de fluxo, a
-- segunda.
-- =====================================================================
SET search_path = viab, public;

CREATE TABLE resultado_projetado (
    rodada_id  bigint NOT NULL REFERENCES rodada ON DELETE CASCADE,
    linha_dre  text   NOT NULL,
    valor      numeric(16,2) NOT NULL,
    ordem      smallint NOT NULL DEFAULT 0,
    PRIMARY KEY (rodada_id, linha_dre)
);
CREATE INDEX ON resultado_projetado (rodada_id, ordem);

-- uma rodada congelada não é recalculada: guarda o resultado que foi aprovado
ALTER TABLE rodada ADD COLUMN congelada boolean NOT NULL DEFAULT false;

COMMENT ON TABLE resultado_projetado IS
 'O DRE da rodada, uma linha por conta sintética. Para o cenário orçado os
  valores são digitados (o estudo original e suas premissas não existem mais);
  para os cenários de projeção, vêm do motor.';
