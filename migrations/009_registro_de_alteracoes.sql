-- =====================================================================
-- 009 — registro de alterações
--
-- Até aqui, mudar uma premissa era um UPDATE: o valor novo entrava e o antigo
-- desaparecia. Num número que decide investimento isso não serve. "Quem baixou
-- a TMA de 18% para 15% antes daquela reunião?" é uma pergunta que uma hora
-- aparece, e a resposta não pode ser "não dá para saber".
--
-- Toda edição feita pelas telas de dados passa por aqui. A importação do
-- Sienge não passa: ela tem o próprio histórico em `importacao`, e registrar
-- 2.612 parcelas uma a uma encheria a tabela sem informar nada.
-- =====================================================================

CREATE TABLE alteracao (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    cenario_id        bigint REFERENCES cenario ON DELETE CASCADE,
    modulo            text NOT NULL,   -- 'premissas','vendas','obra','terreno','unidades','cadastro'
    entidade          text NOT NULL,   -- a linha mexida: 'premissa','tabela_venda','unidade:1203'
    campo             text NOT NULL,   -- 'ret', 'perc_chaves', 'preco_bruto'
    valor_anterior    text,            -- NULL quando a linha não existia
    valor_novo        text,            -- NULL quando a linha foi apagada
    autor             text NOT NULL,
    em                timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON alteracao (empreendimento_id, em DESC);
CREATE INDEX ON alteracao (cenario_id, em DESC);

COMMENT ON TABLE alteracao IS
 'Quem mudou o quê, quando, e de qual valor para qual. Só as edições feitas
  pelas telas de dados — a importação tem o próprio histórico em `importacao`.';

COMMENT ON COLUMN alteracao.valor_anterior IS
 'Texto, não numérico, de propósito: a mesma tabela registra número, data,
  booleano e texto livre. O que importa aqui é ser legível, não ser somável.';
