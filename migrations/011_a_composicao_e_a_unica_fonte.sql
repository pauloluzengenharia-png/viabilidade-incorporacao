-- =====================================================================
-- 011 — a composição é a única fonte do valor do setor
--
-- A migration 010 converteu cada premissa de valor fixo numa composição de um
-- item só e deixou a premissa antiga onde estava, como rede de proteção: se a
-- conversão tivesse errado, o valor original ainda estaria lá.
--
-- Ela não errou — os testes contra a planilha continuaram passando com os
-- mesmos centavos. E agora a rede vira armadilha: com os dois valores no banco,
-- esvaziar uma composição faria a linha voltar ao número antigo em vez de ir a
-- zero. Quem apagou os itens de propósito veria o custo ressuscitar sozinho.
--
-- Então a premissa sai. A partir daqui, o valor de um setor de custo é a soma
-- da sua composição, e não existe segundo lugar onde procurá-lo.
-- =====================================================================

DELETE FROM premissa p
 USING setor_custo s
 WHERE s.codigo = p.chave;
