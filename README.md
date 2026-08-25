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

## O que não entra no repositório

`dados/` está inteiro no `.gitignore`. Ali moram as três coisas que são
informação da empresa e não código:

| arquivo | o que é |
|---|---|
| `kiev.xlsx` | a planilha de origem |
| `kiev.json` | parâmetros da SPE: viabilidade aprovada, contas de mútuo, premissas |
| `kiev_esperado.json` | os valores que a planilha calculava, usados pelos testes |
| `ACHADOS-KIEV.md` | os números concretos por trás de cada decisão de modelagem |

Sem eles o código roda e os testes são **pulados**, não quebrados. Copie a pasta
`dados/` do pacote entregue para dentro do clone antes de migrar.

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
| `/novo` | **cadastro guiado**: cria um estudo do zero, com a explicação de cada campo |
| `/guia` | **como preencher cada dado**: o manual, campo a campo |
| `/admin/carga` | **carga inicial**: sobe a planilha + os parâmetros e monta a primeira SPE |
| `/api/docs` | a API, documentada |

## Subir no Render

O banco já existe: **viabilidade-db** (Postgres 16, Oregon, plano free) na
workspace `pauloluz.engenharia@gmail.com`.

> O plano free do Postgres do Render **expira em 24/09/2026**. Antes disso,
> troque para `basic_256mb` (US$ 6/mês) no dashboard do banco — se expirar, os
> dados são apagados.

1. Crie o repositório **na conta `pauloluzengenharia-png`** — é a que o Render
   já enxerga, pelo LM Platinum. Em github.com/new: nome `viabilidade-incorporacao`,
   privado, sem README. Depois, aqui dentro:

   ```bash
   git remote add origin https://github.com/pauloluzengenharia-png/viabilidade-incorporacao.git
   git push -u origin main
   ```

2. No dashboard do Render: **New → Blueprint**, aponte para o repositório. Ele
   lê o `render.yaml`, sobe o serviço web e liga a `DATABASE_URL` ao banco que
   já existe. Não precisa copiar credencial nenhuma.

3. No painel do serviço, em **Environment**, defina `VIAB_SENHA`. Enquanto ela
   não existir o sistema responde 503 em tudo — é o comportamento desejado, não
   uma falha.

4. Entre no sistema e vá em **`/admin/carga`**. Suba a planilha da SPE e o
   `dados/kiev.json`: a tela roda a mesma migração do `migrar_kiev.py` dentro do
   servidor e devolve o registro linha a linha. Os dois arquivos são apagados do
   disco do servidor assim que a carga termina — os dados ficam no banco.

   Esse caminho existe para a senha do banco não precisar sair do painel do
   Render. Quem preferir a linha de comando pode continuar rodando, da própria
   máquina, com a *External Database URL* exportada em `DATABASE_URL`:

   ```bash
   python3 migrar_kiev.py "caminho/para/26. Incorrido SPE Kiev.xlsx" dados/kiev.json
   python3 -m pytest tests -q          # confere as linhas contra a planilha
   ```

   Do segundo empreendimento em diante, tudo entra pela tela de importação.

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
  glossario.py   ← o que é cada dado: fonte única do guia E da ajuda nas telas
  novo_estudo.py ← criação de um estudo do zero, com validação que lista tudo
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
Todos estão cobertos por teste. Os valores concretos que motivaram cada decisão
estão em `dados/ACHADOS-KIEV.md`, que não é versionado.

- **A obra desembolsa o custo inteiro.** Na planilha, a linha do desembolso na
  projeção é um conjunto de valores colados, desligado do cronograma — mexer no
  cronograma não mudava o fluxo. Aqui o desembolso é a curva física aplicada ao
  custo, e fecha.
- **Um orçamento de obra só.** A planilha usava o custo de uma versão do
  orçamento com a curva de outra, vinda de outro empreendimento. Agora o total e
  a curva pertencem ao mesmo `orcamento_obra`.
- **A TIR não finge existir.** A planilha reporta uma TIR alta a partir de uma
  série com valores deslocados à mão para uma célula "início". Sem esse ajuste o
  fluxo troca de sinal várias vezes. O sistema devolve `NULL` quando não há raiz
  real e publica a MTIR ao lado.
- **O realizado vem da fonte, não do cache do Excel.** Uma das células de SUMIFS
  guardava um resultado desatualizado em relação aos próprios lançamentos. O
  sistema soma os movimentos; a planilha mostrava o cache.
- **A comissão realizada respeita a data de corte.** A planilha soma a comissão
  de todo contrato assinado, inclusive os posteriores ao fechamento do mês.
- **Unidade fora do VGV precisa de motivo.** O cadastro do Sienge e a lista que a
  planilha usava para o VGV não eram a mesma lista, e a diferença sumia entre
  duas abas. Agora é uma flag com justificativa.
- **A tabela de venda tem de fechar 100%** e **a curva física de cada atividade
  tem de somar 100%** — as duas travas são `CHECK`/trigger no banco.
- **Resultado ≠ fluxo.** Somar as colunas mensais não dá a linha do DRE: o fluxo
  só vê o caixa dentro do horizonte, e o resultado inclui a permuta (que não
  passa por caixa) e as parcelas que caem depois. São duas tabelas.

Uma diferença é só de arredondamento e não vale correção: a planilha calcula
"outras despesas administrativas" com um percentual de sete casas, resultado de
uma divisão feita uma vez. O sistema usa o percentual redondo.

## Acesso

O sistema abre numa **tela de login** (`/entrar`). Quem acerta usuário e senha
recebe um cookie de sessão válido por 12 horas; qualquer outra rota redireciona
para o login enquanto esse cookie não existir. As rotas `/api/*` respondem 401
seco, sem redirecionar — elas são consumidas por programa, não por navegador.

| variável | o que é |
|---|---|
| `VIAB_SENHA` | senha de acesso. **Não mora no repositório** — é definida no painel do Render, em Environment |
| `VIAB_USUARIO` | usuário; o padrão é `mmi` |
| `VIAB_SEGREDO` | opcional; entra na derivação da chave de assinatura |

Três decisões que valem explicar:

- **Fail-closed.** Sem `VIAB_SENHA` no ambiente o sistema responde 503 em tudo
  menos no health check, com uma página explicando como configurar. Um sistema
  que guarda a viabilidade e o incorrido de uma SPE não pode ficar aberto por
  esquecimento de variável de ambiente.
- **A senha não vira cookie.** O cookie carrega `{usuário, validade}` assinados
  com HMAC-SHA256 sobre uma chave derivada da senha. Quem interceptar o cookie
  não descobre a senha — e **trocar a senha no painel invalida todas as sessões
  abertas**, porque a chave muda junto.
- **Comparação em tempo constante.** `hmac.compare_digest` nos dois campos, para
  o tempo de resposta não entregar o tamanho da senha.

O cookie é `HttpOnly` (fora do alcance de JavaScript), `SameSite=Lax` (não viaja
em requisição vinda de outro site) e `Secure` quando a conexão é HTTPS. O
destino pós-login é validado contra *open redirect*: só caminhos que começam com
uma única barra.

Ainda é **uma credencial só para todo mundo**. Se virar multiusuário, o caminho
é uma tabela de usuários com hash por linha — a estrutura de sessão já suporta,
porque o cookie já carrega o usuário.

## Identidade visual

A folha de estilo segue o guia de marca da MMI: preto, areia `#e0d0bf`, cinza
`#a0a0a0` e off-white `#F4F4F4`, com o logotipo no cabeçalho, no login e no
rodapé. A tipografia é Outfit (substituta livre da Gilroy do guia); os números
usam Inter com figuras tabulares, porque coluna de dinheiro precisa alinhar e
monoespaçada dava ao sistema cara de terminal.

Uma decisão que vale registrar: a marca é neutra de propósito, e neutro não
resolve gráfico — duas séries só se distinguem por matiz. Então a paleta é
dividida. **Interface** (fundo, texto, botão, aba, tarja) usa só cor de marca.
**Dado** (as duas séries do fluxo, o sinal do número, os marcadores) usa um par
petróleo/terracota que passou no `validate_palette`: faixa de luminosidade, piso
de croma, separação sob protanopia e deuteranopia e contraste contra a
superfície, nos dois modos. O par aparece pouco e sempre pequeno.

Todo texto tem contraste ≥ 4,5:1 contra o fundo em que assenta, claro e escuro.

## Onde mora a explicação de cada campo

`app/glossario.py` é a fonte única. A tela `/guia` monta o manual a partir dele,
a tabela de viabilidade pendura nele o "o que é" de cada linha do DRE, e o
formulário de cadastro puxa dele o texto do "?" ao lado de cada campo. Escrever
a explicação em dois lugares seria garantir que um dos dois envelhece.

Cada verbete responde sempre às mesmas quatro perguntas — o que é, de onde vem,
como preencher e o que costuma dar errado — porque são as quatro que aparecem
quando alguém senta para preencher.

## Um estudo do zero

`/novo` cobre o caso anterior à planilha e ao Sienge: o terreno em avaliação, o
lançamento que ainda vai à diretoria. Duas simplificações deliberadas, porque a
alternativa seria pedir na primeira tela um dado que nessa fase não existe:

- **O estoque nasce homogêneo.** Informa-se quantas unidades e a área privativa
  total; o sistema cria N unidades de área média ao preço de tabela. Isso basta
  para VGV, fluxo e indicadores. Quando o cadastro real chegar do Sienge, a
  importação substitui as unidades sintéticas.
- **A curva de obra vem de um formato** — linear, S suave ou S acentuada — em
  vez do cronograma físico-financeiro detalhado, que entra depois pelo orçamento.

O que não é simplificado: as travas continuam valendo. A tabela de venda soma
100% e a curva soma 100%, aqui como em qualquer outro caminho. A validação
devolve a lista completa de problemas de uma vez, em vez de parar no primeiro —
quem preenche 40 campos merece ver os quatro erros juntos.

## O que ainda não existe

- **Usuários individuais.** Hoje é uma credencial só para todo mundo. Não há
  registro de quem mexeu em qual premissa.
- **Edição das premissas pela tela.** `/novo` cria; depois disso, mudar uma
  premissa ainda é `UPDATE` no banco seguido de Recalcular.
- **Cronograma de obra detalhado pela tela.** A curva por formato resolve o
  estudo inicial; a EAP de 21 itens continua entrando por migração ou importação.
- **Financiamento à produção.** O motor tem o bloco (`_linhas_financiamento`,
  liberação por evolução de obra, juros e amortização) e ele está desligado
  porque a Kiev tem limite zero. Falta a tela para cadastrar o contrato.
