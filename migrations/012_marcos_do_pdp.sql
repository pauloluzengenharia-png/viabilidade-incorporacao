-- =====================================================================
-- 012 — os marcos do PDP dentro do estudo
--
-- Até aqui o estudo sabia QUANTO cada setor custa e chutava QUANDO. A
-- regularização fundiária saía "diluída na curva da obra"; a decoração, nos
-- seis últimos meses; o marketing, ao longo do canteiro. São regras razoáveis
-- e nenhuma delas é verdade: o cronograma do PDP diz que a regularização
-- fundiária ocupa 60 meses de calendário, que o comercial se resolve em 11, e
-- que o financeiro/contábil ainda tem marco cinco anos depois do lançamento.
--
-- Diluir esses custos pela obra adianta desembolso que acontece lá na frente —
-- e a exposição máxima de caixa, que é o número que decide o aporte, sai
-- errada nas duas pontas.
--
-- A partir daqui o estudo lê o cronograma. Cada área do PDP aponta para um
-- setor de custo; cada marco carrega a sua data; e o dinheiro de um setor cai
-- nos meses em que a área tem marco terminando. Quem quiser mais precisão
-- amarra o item da composição a um marco específico, e aí o valor sai na data
-- daquele marco, não numa média.
--
-- Nada disso é obrigatório: setor sem marco continua com a regra antiga. O
-- cronograma melhora a projeção quando existe, e não quebra nada quando falta.
-- =====================================================================

-- --------------------------------------------------------------- áreas
-- O de-para entre a área que executa (PDP) e o setor que paga (viabilidade).
-- Uma área pode não ter setor — Orçamento produz o preço dos outros e não tem
-- custo próprio a compor — e um setor pode receber marcos de mais de uma área.
CREATE TABLE area_pdp (
    codigo   text PRIMARY KEY,          -- o id da área no PDP, como texto
    nome     text NOT NULL,
    setor    text REFERENCES setor_custo ON DELETE SET NULL,
    papel    text,                      -- por que esta área importa no estudo
    ordem    smallint NOT NULL DEFAULT 0
);

COMMENT ON COLUMN area_pdp.setor IS
 'O setor de custo que paga o trabalho desta área. NULL quando a área não gera
  custo direto — Orçamento, Planejamento, Qualidade — ou quando o custo dela
  já está em outro lugar do estudo, como Engenharia, que é a EAP da obra.';

-- -------------------------------------------------------------- marcos
CREATE TABLE marco (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    pdp_id            text   NOT NULL,          -- o #3210 do PDP, sem o #
    nome              text   NOT NULL,
    area_codigo       text   REFERENCES area_pdp ON DELETE SET NULL,
    processo          text,
    fase              text,
    inicio            date,
    fim               date,
    duracao           integer,
    progresso         smallint NOT NULL DEFAULT 0,
    critico           boolean  NOT NULL DEFAULT false,
    sincronizado_em   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (empreendimento_id, pdp_id)
);

CREATE INDEX ON marco (empreendimento_id, area_codigo);
CREATE INDEX ON marco (empreendimento_id, fim);

COMMENT ON TABLE marco IS
 'Espelho dos marcos do PDP. É cópia, não fonte: quem manda no cronograma é o
  PDP, e o estudo apenas lê. Por isso cada sincronização substitui o conjunto
  inteiro do empreendimento.';

COMMENT ON COLUMN marco.critico IS
 'No caminho crítico do cenário reprogramado. Um marco crítico que atrasa move
  a data de tudo que vem depois — e, agora que o desembolso segue os marcos,
  move também a curva de caixa.';

CREATE TABLE marco_dependencia (
    marco_id     bigint NOT NULL REFERENCES marco ON DELETE CASCADE,
    predecessor  text   NOT NULL,        -- pdp_id do predecessor
    tipo         text   NOT NULL DEFAULT 'TI'
                 CHECK (tipo IN ('TI', 'II', 'TT', 'IT')),
    defasagem    integer NOT NULL DEFAULT 0,
    PRIMARY KEY (marco_id, predecessor, tipo)
);

COMMENT ON COLUMN marco_dependencia.tipo IS
 'TI término→início, II início→início, TT término→término, IT início→término.
  Guardado para explicar por que um marco tem a data que tem — o estudo não
  recalcula cronograma, quem faz isso é o PDP.';

-- --------------------------------------------------- histórico das cargas
CREATE TABLE sincronizacao_pdp (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    quando            timestamptz NOT NULL DEFAULT now(),
    autor             text NOT NULL,
    ok                boolean NOT NULL,
    marcos            integer NOT NULL DEFAULT 0,
    dependencias      integer NOT NULL DEFAULT 0,
    mensagem          text
);

CREATE INDEX ON sincronizacao_pdp (empreendimento_id, quando DESC);

-- ------------------------------------------------------------- ligações
ALTER TABLE empreendimento ADD COLUMN pdp_project_id integer;
COMMENT ON COLUMN empreendimento.pdp_project_id IS
 'O project_id deste empreendimento no PDP. Sem ele não há sincronização.';

ALTER TABLE composicao_item
    ADD COLUMN marco_id bigint REFERENCES marco ON DELETE SET NULL;
COMMENT ON COLUMN composicao_item.marco_id IS
 'Quando preenchido, este item desembolsa no mês em que o marco termina, e não
  na distribuição média do setor. É para o gasto que tem data conhecida: a taxa
  que se paga no protocolo, o evento que acontece num dia.';

-- ------------------------------------------- desembolso guiado por marcos
ALTER TABLE setor_custo DROP CONSTRAINT setor_custo_desembolso_check;
ALTER TABLE setor_custo ADD CONSTRAINT setor_custo_desembolso_check
    CHECK (desembolso IN ('obra', 'lancamento', 'a_vista', 'entrega', 'marcos'));

COMMENT ON COLUMN setor_custo.desembolso IS
 'Quando o dinheiro sai. marcos = nos meses em que a área tem marco terminando
  (precisa de cronograma sincronizado; sem ele, cai na regra antiga do setor).
  obra = acompanha a curva física; lancamento = diluído até o lançamento;
  a_vista = tudo no primeiro mês; entrega = concentrado no fim da obra.
  Muda o caixa, nunca o resultado.';

-- ---------------------------------------------------- o setor que faltava
-- Dez marcos de CX e Relacionamento — visitas, vistorias, NPS, AGI, repasse —
-- não tinham onde cair no estudo. Custam brinde, evento, equipe e assistência
-- técnica, e acontecem quase todos depois da última chave, quando a obra já
-- não desembolsa nada. Sem setor próprio, esse dinheiro simplesmente não
-- aparecia na projeção.
INSERT INTO setor_custo (codigo, nome, linha_dre, bloco, desembolso, ordem, resumo) VALUES
  ('pos_entrega', 'Pós-entrega e relacionamento',
   '(-) Pós-entrega e relacionamento', 'Incorporação e comercial', 'marcos', 80,
   'O que se gasta depois da chave: vistorias e revistorias, brindes e evento de entrega, assembleia de instalação, acompanhamento do síndico, pesquisa de satisfação e o apoio ao repasse bancário dos clientes.');

-- --------------------------------------------------------- as áreas do PDP
-- Os códigos são os do PDP e valem para todos os projetos; o de-para com o
-- setor é decisão da casa e está explicado em `papel`.
INSERT INTO area_pdp (codigo, nome, setor, papel, ordem) VALUES
  ('6',  'Regularização Fundiária', 'regularizacao_fundiaria',
   'Matrículas, enfiteuse, remembramento e a propriedade da SPE. Do 1º Registro em diante o gasto é de cartório de registro e pertence à legalização — separe ao compor.', 10),
  ('9',  'Legalização Imobiliária', 'legalizacao',
   'Licenças, alvarás, concessionárias, kit incorporação e habite-se. É a maior área do cronograma.', 20),
  ('19', 'Financeiro/Contábil', 'legalizacao',
   'IPTU quitado, unificado e desmembrado, e o CNO da demolição: tributo do imóvel, que o estudo cobra dentro da legalização.', 30),
  ('14', 'Projeto', 'projetos_e_outros',
   'Contratos de projetista, do conceito ao executivo. É o que o arquiteto cobra para desenhar, não o que o órgão cobra para aprovar.', 40),
  ('20', 'Projetos', 'projetos_e_outros',
   'Mesma função da área Projeto — o PDP tem as duas cadastradas.', 50),
  ('10', 'Produto', 'projetos_e_outros',
   'Briefing, estudo de produto, anteprojeto e os interiores das áreas comuns. A parte de interiores é candidata a virar decoração quando a casa decidir o que decoração inclui.', 60),
  ('13', 'Marketing', 'marketing_propaganda',
   'Branding, plano, materiais e os três eventos. Nenhum marco de marketing é do stand: o stand aparece só no layout de canteiro e na comunicação do tapume, que são da Engenharia.', 70),
  ('11', 'Comercial', 'despesas_comerciais',
   'Tabela, campanha e treinamento de corretores. Onze meses de calendário, concentrado — o oposto do que a diluição pela obra assume.', 80),
  ('16', 'CX', 'pos_entrega',
   'Visitas de cliente durante a obra e a satisfação depois da entrega.', 90),
  ('17', 'Relacionamento', 'pos_entrega',
   'Vistorias, primeira chave, AGI e o encaminhamento do financiamento dos clientes.', 100),
  ('3',  'Engenharia', NULL,
   'A obra em si, já orçada pela EAP. Os marcos servem para conferir a curva física, não para compor preço.', 110),
  ('5',  'Novos Negócios', NULL,
   'Posse e fechamento do terreno. O valor vem do contrato, não de composição.', 120),
  ('8',  'Controladoria', NULL,
   'Viabilidade, metas de caixa e financiamento. Entra no estudo como taxa de administração, que é percentual.', 130),
  ('7',  'Orçamento', NULL,
   'Produz o preço dos outros setores. Não tem custo próprio a compor — tem calendário a cumprir.', 140),
  ('1',  'Geral', NULL, 'Sem marco no Gante.', 200),
  ('2',  'Planejamento', NULL, 'Sem marco no Gante.', 210),
  ('4',  'Qualidade', NULL, 'Sem marco no Gante.', 220),
  ('12', 'Financeito/Contábil', 'legalizacao',
   'Duplicata com erro de digitação da área 19, mantida para não perder marco antigo.', 230),
  ('18', 'TBD', NULL, 'Área provisória do PDP.', 240);

COMMENT ON TABLE area_pdp IS
 'De-para entre quem executa (área do PDP) e quem paga (setor de custo do
  estudo). É aqui que se resolve a pergunta "de qual linha do resultado sai o
  dinheiro deste marco".';
