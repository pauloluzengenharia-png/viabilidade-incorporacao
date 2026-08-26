-- =====================================================================
-- 013 — o cronograma como Gantt: custo por marco e simulação de atraso
--
-- Duas perguntas que a lista de marcos não respondia.
--
-- **Quanto custa cada marco.** O setor sabe o seu total; o marco não sabia
-- nada. Dividir o total pelo número de marcos seria uma conta do sistema, e
-- errada em muitos casos — uma taxa de cartório não custa o mesmo que um
-- registro de incorporação. Então cada setor informa o custo médio dos seus
-- marcos, e o sistema compara essa média com o total da composição: quando as
-- duas contas não fecham, a tela diz. É a mesma disciplina da tabela de venda
-- que precisa somar 100%.
--
-- **E se atrasar.** Um marco que escorrega empurra tudo que depende dele, e o
-- dinheiro daquele setor sai mais tarde. Simular isso é mexer em data, e data
-- é do PDP — por isso a simulação não escreve por cima do cronograma: ela vira
-- um cenário próprio, com os deslocamentos guardados aqui. O cenário roda o
-- motor inteiro e fica ao lado do realista para comparar, que é o que um
-- cenário serve para fazer.
-- =====================================================================

ALTER TABLE setor_custo ADD COLUMN custo_medio_marco numeric(16,2);

COMMENT ON COLUMN setor_custo.custo_medio_marco IS
 'Quanto custa, em média, um marco deste setor. Informado pela casa, não
  deduzido pelo sistema. Serve para dar valor a cada barra do Gantt; o total do
  resultado continua vindo da composição. Quando média × nº de marcos diverge do
  total da composição, a tela mostra a diferença em vez de escolher um dos dois.';

-- ------------------------------------------------- cronograma do cenário
CREATE TABLE cenario_marco_ajuste (
    cenario_id bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    marco_id   bigint NOT NULL REFERENCES marco ON DELETE CASCADE,
    dias       integer NOT NULL,
    origem     text NOT NULL DEFAULT 'simulacao'
               CHECK (origem IN ('simulacao', 'propagacao')),
    PRIMARY KEY (cenario_id, marco_id)
);

CREATE INDEX ON cenario_marco_ajuste (cenario_id);

COMMENT ON TABLE cenario_marco_ajuste IS
 'O quanto cada marco anda, em dias, neste cenário. Cenário sem linha aqui usa
  o cronograma do PDP como está — que é o caso do realista.';

COMMENT ON COLUMN cenario_marco_ajuste.origem IS
 'simulacao = o atraso que a pessoa informou; propagacao = o que a rede de
  dependências empurrou por causa dele. Guardar os dois separados é o que
  permite responder depois "eu atrasei um marco ou dezoito?".';
