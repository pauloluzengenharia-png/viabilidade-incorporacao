# Sistema de viabilidade de incorporação

Substitui a planilha `26. Incorrido SPE Kiev.xlsx` por um sistema com banco,
motor de cálculo testado e uma fronteira explícita entre as três coisas que a
planilha misturava na mesma linha:

| | o que é | onde mora |
|---|---|---|
| **Premissa** | o que a diretoria escolhe | `cenario`, `premissa`, `tabela_venda`, `plano_venda`, `orcamento_obra` |
| **Realizado** | o extrato do Sienge | `movimento_realizado`, `parcela_recebida` |
| **Projeção** | o que o motor calcula | `rodada`, `resultado_projetado`, `fluxo_projetado`, `indicador` |

Na planilha essas três coisas eram, respectivamente, a coluna `E`, as colunas
`J:AC` e as colunas `AF:DQ` da mesma linha. Funciona enquanto uma pessoa souber
onde é a fronteira. Aqui a fronteira é uma data (`empreendimento.mes_corte_realizado`)
e um parâmetro de consulta.

## Rodar local

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://usuario@localhost/viabilidade

python3 migrar_kiev.py dados/kiev.xlsx     # cria o schema e carrega a SPE Kiev
python3 -m pytest tests -q                 # 47 conferências contra a planilha
uvicorn app.main:app --reload              # http://localhost:8000
```

As migrations rodam sozinhas no startup — não há passo manual de schema.

## Telas

| rota | o que faz |
|---|---|
| `/` | empreendimentos com os indicadores do cenário principal |
| `/empreendimento/{id}` | **viabilidade**: orçado × atualizado × realizado × a realizar |
| `/empreendimento/{id}/cenarios` | comparação dos cenários lado a lado |
| `/empreendimento/{id}/fluxo` | fluxo de caixa mensal, exposição e aporte |
| `/empreendimento/{id}/importar` | upload dos exports do Sienge, com histórico |
| `/api/docs` | a API, documentada |

## Subir no Render

O banco já existe: **viabilidade-db** (Postgres 16, Oregon, plano free) na
workspace `pauloluz.engenharia@gmail.com`.

> O plano free do Postgres do Render **expira em 24/09/2026**. Antes disso,
> troque para `basic_256mb` (US$ 6/mês) no dashboard do banco — se expirar, os
> dados são apagados.

1. Crie o repositório no GitHub e dê push (o `git init` e o primeiro commit já
   estão feitos aqui dentro):

   ```bash
   git remote add origin git@github.com:SEU-USUARIO/viabilidade.git
   git push -u origin main
   ```

2. No dashboard do Render: **New → Blueprint**, aponte para o repositório. Ele
   lê o `render.yaml`, sobe o serviço web e liga a `DATABASE_URL` ao banco que
   já existe. Não precisa copiar credencial nenhuma.

3. Carregue a SPE Kiev **uma vez**, da sua máquina, apontando para o banco de
   produção (a *External Database URL* está no dashboard do banco):

   ```bash
   export DATABASE_URL="postgresql://...@oregon-postgres.render.com/viabilidade_db"
   python3 migrar_kiev.py "caminho/para/26. Incorrido SPE Kiev.xlsx"
   python3 -m pytest tests -q          # confere as 47 linhas contra a planilha
   ```

   Daí em diante os próximos empreendimentos entram pela tela de importação, sem
   passar por planilha.

O plano free do serviço web hiberna depois de 15 minutos sem acesso e leva uns
30 segundos para acordar. Para uso de verdade, `starter` (US$ 7/mês) resolve.

## Arquitetura

```
app/
  motor/       ← cálculo puro: não sabe que existe banco nem HTTP
    modelo.py    entidades e premissas (cada campo aponta a célula de origem)
    engine.py    VGV, DRE, coortes de recebíveis, fluxo mensal, VPL/TIR/MTIR
  repositorio.py ← única camada que conhece banco E motor
  servico.py     ← roda um cenário e persiste a rodada (com hash das entradas)
  importadores/
    sienge.py    normalização — hoje lê xlsx, amanhã lê a API, mesmas regras
    gravar.py    gravação idempotente
  main.py        ← FastAPI: telas e API
migrations/      ← .sql numerados, aplicados uma vez cada
tests/           ← as conferências contra a planilha
```

O motor é puro de propósito: dá para testá-lo sem banco, e dá para trocar o
banco sem tocar no cálculo.

## Sienge: hoje upload, amanhã API

Os `normalizar_*` de `app/importadores/sienge.py` recebem **lista de dicts** —
o formato em que tanto o export quanto o JSON da API chegam. O `_campo()`
aceita os dois jeitos de nomear (`Value.dueDate` do Power Query e `dueDate` da
API). Ligar a API é escrever um cliente que devolve as mesmas listas; nem o
motor nem o serviço mudam.

Três regras do Sienge que estão codificadas ali e custam caro a redescobrir:

1. **`item de orçamento`** — se a unidade construtiva é OBRA, tudo cai em
   `1 - OBRA`; senão usa o item da planilha de custo; e só se não houver
   planilha de custo é que usa a categoria financeira.
2. **Os dois rateios têm escalas diferentes.** `financialCategoryRate` vem em
   pontos percentuais (100 = integral) e `%` vem em fração (1 = integral).
   Tratá-los igual erra 18 lançamentos na base da Kiev.
3. **`bankMovementId` não é chave.** Um pagamento rateado entre duas categorias
   volta em duas linhas com o mesmo id — e pode voltar duas vezes na *mesma*
   categoria. A chave é (movimento, conta, sequência).

## O que o sistema faz diferente da planilha, de propósito

Cada item abaixo é um caso em que reproduzir a planilha seria reproduzir um erro.
Todos estão cobertos por teste.

- **A obra desembolsa o custo inteiro.** Na planilha, a linha 37 da projeção é um
  conjunto de valores colados, desligado do cronograma — mexer no cronograma não
  mudava o fluxo. Aqui o desembolso é a curva física aplicada ao custo, e fecha.
- **Um orçamento de obra só.** A planilha usava custo de R$ 104,0 M com a curva de
  um orçamento de R$ 70,6 M de outro empreendimento. Agora o total e a curva
  pertencem ao mesmo `orcamento_obra`.
- **A TIR não finge existir.** A planilha reporta 163,7% a partir de uma série com
  R$ 1,66 M deslocados à mão para uma célula "início". Sem esse ajuste o fluxo
  troca de sinal várias vezes. O sistema devolve `NULL` quando não há raiz real e
  publica a MTIR ao lado.
- **O terreno realizado é R$ 1.863.762,81, não R$ 3.213.762,81.** A célula `J35`
  da planilha guarda um SUMIFS cujo cache ficou R$ 1,35 M acima da própria fonte
  e nunca foi recalculado.
- **A comissão realizada respeita a data de corte.** A planilha soma a comissão de
  todo contrato assinado, inclusive um de 03/08/2026 dentro de um realizado
  fechado em 31/07/2026 (R$ 47.190,48 que ainda não aconteceram).
- **Unidade fora do VGV precisa de motivo.** `SALA 1 - G 02` e `SALA 2 - G 02`
  existem no Sienge e não estavam na aba Comercial; `ROOFTOP` estava no VGV sem
  cadastro no Sienge. Agora isso é uma flag com justificativa, não uma diferença
  invisível entre duas abas.
- **A tabela de venda tem de fechar 100%** e **a curva física de cada atividade
  tem de somar 100%** — as duas travas são `CHECK`/trigger no banco.
- **Resultado ≠ fluxo.** Somar as colunas mensais não dá a linha do DRE: o fluxo
  só vê o caixa dentro do horizonte, e o resultado inclui a permuta (que não
  passa por caixa) e as parcelas que caem depois. São duas tabelas.

Uma diferença é só de arredondamento e não vale correção: a planilha calcula
"outras despesas administrativas" com `1,5001661%` — dígito espúrio de uma divisão
feita uma vez. O sistema usa 1,5% redondo, e o lucro fica R$ 173 acima.

## O que ainda não existe

- **Correção monetária das parcelas.** A planilha projeta em valores nominais
  apesar de os contratos terem indexador no Sienge. Se for corrigir, é um campo
  em `parcela_receber` e uma série de índice mensal.
- **Autenticação.** O sistema está aberto — antes de expor com dados reais,
  colocar um login na frente.
- **Financiamento à produção.** O motor tem o bloco (`_linhas_financiamento`,
  liberação por evolução de obra, juros e amortização) e ele está desligado
  porque a Kiev tem limite zero. Falta a tela para cadastrar o contrato.
