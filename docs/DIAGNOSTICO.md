# Diagnóstico do Projeto

## Fluxo Operacional

Em análise do fluxo operacional pelos arquivos: [`apresentacoes.json`](../data/original/apresentacoes.json), [`pagamentos.json`](../data/original/pagamentos.json), [`tribunal.csv`](../data/original/tribunal.csv) e [`hub.csv`](../data/original/hub.csv), foram encontradas as seguintes divergências ordenadas da maior para a menor criticidade estimada:

---
### 1. Importação aditiva sem proteção contra repetição e eventos com conteúdo diferente tendo um mesmo identificador
**Observação:**
Com base no [`ENUNCIADO.md`](ENUNCIADO.md), o fluxo descrito apresenta risco de contabilização repetida, e o esquema do banco [`schema.sql`](../docs/schema.sql) não possui uma restrição que impeça eventos duplicados. Como exemplo, no arquivo [`pagamentos.json`](../data/original/pagamentos.json), o evento `ev-002` aparece duas vezes com conteúdo idêntico, nas linhas 11–16 e 27–32, podendo acrescentar R$ 2.500,50 indevidamente se ambas as ocorrências forem contabilizadas. Já `ev-001`, nas linhas 3–8 e 67–72, apresenta valores de R$ 15.000,00 e R$ 15.500,00 e horários de atualização diferentes. Isso pode representar uma retificação, mas depende de confirmação do tribunal.

**Impacto direto:**
A repetição dos eventos, combinada com o processamento aditivo sem proteção suficiente, pode gerar um total pago maior que o real. A ausência da restrição de unicidade para o campo `evento_id` no banco permite que a duplicidade seja persistida.

**Tratamento:**
Ignorar repetições idênticas e sinalizar conteúdos diferentes para o mesmo identificador até confirmar a regra de atualização com o tribunal.
Fazer uma mudança no schema.sql para adicionar uma restrição de unicidade para o campo `evento_id`, após confirmação do escopo em conjunto com o tribunal.

As opções abaixo são alternativas. Para uma nova tabela, alterar a definição da coluna dentro de `CREATE TABLE pagamento` no `schema.sql`:

```sql
evento_id TEXT NOT NULL UNIQUE,
```

Para uma tabela existente, adicionar a restrição com `ALTER TABLE` após resolver as duplicações já gravadas:

```sql
ALTER TABLE pagamento
    ADD CONSTRAINT pagamento_evento_unico UNIQUE (evento_id);
```

O SQL é uma proposta e não foi executado. A restrição não resolve sozinha retificações: o importador deve comparar os conteúdos. Sem acesso ao código, não é possível confirmar todos os controles já existentes.

---
### 2. Estorno contradiz a documentação do tribunal
**Observação:**
Em [`pagamentos.json`](../data/original/pagamentos.json), linhas 51-56, o evento `ev-006` é um pagamento de R$ 5.000,00 e, nas linhas 59-64, o evento `ev-007` é um estorno de R$ 5.000,00 no mesmo processo, no dia seguinte. A documentação do tribunal, em [`DOCUMENTACAO_TRIBUNAL.md`](../docs/DOCUMENTACAO_TRIBUNAL.md), linha 15, diz que o tipo é sempre `pagamento`, no banco [`schema.sql`](../docs/schema.sql), linha 12, é aceito `estorno`. Mas como há divergência entre a documentação e os dados recebidos, é necessário confirmar a regra de processamento dos estornos e se existe vínculo com o pagamento original.

**Impacto direto:**
Se o estorno for ignorado, e sua representação for a de reversão do pagamento, isso manterá o total pago incorreto em R$ 5.000,00. Se o importador somar ambos os eventos, contabilizará R$ 10.000,00 indevidamente.

**Tratamento:**
Confirmar com o tribunal o significado do estorno e sua vinculação ao pagamento original. Se representar reversão, subtrair seu valor do total pago. Até o esclarecimento, preservar o evento como pendente e registrar o motivo.

---

### 3. Falhas ocultas na importação
**Observação:**
Em [`ENUNCIADO.md`](ENUNCIADO.md), linha 19, eventos não importados são ignorados sem aviso. Há eventos incompatíveis com as restrições do banco ou sem informação suficiente para importação: `ev-004` linhas 35-40 com valor nulo, `ev-009` linhas 83-88 com valor negativo e `ev-010` linhas 91-96 sem processo. A ausência de comunicação de rejeições amplifica as consequências de problemas de dados e contrato.

**Impacto direto:**
Não é possível afirmar quais registros o script efetivamente rejeitou sem seus logs.
Saldo incompleto sem indicação da perda, impossibilidade de conciliar recebidos versus processados e dificuldade de corrigir ou reprocessar os rejeitados.

**Tratamento:**
Salvar os registros rejeitados ou pendentes com sua localização na origem, motivo específico e contagens por resultado. Alertar a operação e permitir reprocessamento após correção. Não substituir valor nulo por zero, inverter sinais ou inventar um processo.

---

### 4. Representação de processo incompatível com cadastro e conciliação
**Observação:**
Em [`schema.sql`](../docs/schema.sql), linha 3, o campo `numero_processo` é definido como `CHAR(20) NOT NULL UNIQUE`, o que implica que o número do processo comporta até 20 caracteres, e a definição também não garante que o conteúdo seja apenas numérico. No entanto, em [`pagamentos.json`](../data/original/pagamentos.json), em sua maioria, os processos possuem máscara de formatação (ex: `0023456-21.2020.8.26.0053`), o que pode causar incompatibilidade ao inserir no banco.

**Impacto direto:**
Se os processos forem inseridos com a máscara, o banco não permitirá a inserção devido à restrição de tamanho, pois o número do processo terá mais de 20 caracteres.

**Tratamento:**
Validar o formato recebido e normalizar o processo para uma representação textual de 20 dígitos, preservando os zeros iniciais. Utilizar essa representação no cadastro e na conciliação, mantendo a entrada original para auditoria.

---

### 5. Divergências de conteúdo e presença entre Tribunal e Hub

**Observação:**

Após considerar a equivalência dos processos com e sem máscara, permanecem diferenças entre [`tribunal.csv`](../data/original/tribunal.csv) e [`hub.csv`](../data/original/hub.csv):

| Divergência | Evidência |
|---|---|
| Valor de face | Processo `0023456-21.2020.8.26.0053`: R$ 30.000,00 no Tribunal e R$ 31.000,00 no Hub, linha 3 de ambos. |
| Ente devedor | Processo `0045678-43.2021.5.02.0011`: União no Tribunal e Município de Santos no Hub, linha 5 de ambos. |
| Ausência no Hub | Processo `0067890-65.2016.8.26.0053`: consta no Tribunal, linha 6, mas não no Hub. Também aparece em `apresentacoes.json`, linhas 23–24, e em `ev-011`. |
| Ausência no Tribunal | Processo `0056789-54.2017.4.03.6100`: consta no Hub, linha 6, mas não no Tribunal. Também aparece em `apresentacoes.json`, linhas 19–20, e em `ev-009`. |

Na linha 2 de cada CSV, há ainda diferenças de apresentação entre `MUNICIPIO DE SAO PAULO` e `Município de São Paulo`. Elas não equivalem ao conflito de conteúdo entre União e Município de Santos.

**Impacto direto:**

A diferença de R$ 1.000,00 pode afetar o saldo remanescente. O ente divergente pode atribuir a dívida ao devedor errado. Ausências podem indicar carteira incompleta ou apenas escopos diferentes de exportação. Não é possível determinar a informação correta somente com esses arquivos.

**Tratamento:**

Conciliar pela chave canônica do processo. Confirmar com tribunal e operação o escopo, as datas de referência e a fonte de autoridade. Preservar conflitos até sua resolução. Padronização de texto não resolve diferenças reais de valor ou devedor; a ausência em uma fonte não autoriza excluir o registro.

---

### 6. Prazo incorreto e dúvida sobre fuso horário

**Observação:**

Em `hub.csv`, linha 2, o processo `0012345-10.2019.8.26.0053` tem apresentação em `2025-04-02` e prazo `2027-12-31`. A apresentação também consta em `apresentacoes.json`, linhas 3–4. Pela regra do enunciado, que inclui 2 de abril no primeiro intervalo, o prazo deveria ser **2026-12-31**.

Outro caso exige confirmação: `2025-04-03T01:00:00Z`, no Hub, linha 6, e nas apresentações, linhas 19–20, corresponde a 02/04/2025 às 22h em UTC−03. O prazo seria 2027 pela data UTC e 2026 se a data de negócio fosse extraída em UTC−03. O fuso aplicável não foi documentado. O Tribunal CSV não contém data de apresentação.

**Impacto direto:**

O primeiro prazo desloca a prioridade da carteira em um ano. A interpretação de fuso pode causar erro semelhante na fronteira do corte.

**Tratamento:**

Recalcular os prazos conforme a regra do case, testando datas antes, no dia e depois de 2 de abril. Confirmar o fuso para registros com horário antes de extrair a data; preservar datas sem horário como datas civis. Documentar a origem de `apresentacoes.json`, que não é citado nominalmente no enunciado nem descrito no manual.

---

### 7. Valor e data fora do formato documentado

**Observação:**

Em `pagamentos.json`, `ev-002` apresenta `"2.500,50"` como texto, nas linhas 13 e 29, e `"10/04/2025"`, nas linhas 14 e 30. A documentação, linhas 13–14, exige valor numérico com ponto decimal e data `AAAA-MM-DD`.

**Impacto direto:**

A conversão do valor pode falhar ou produzir um valor incorreto. A data pode ser interpretada como 10 de abril ou 4 de outubro conforme a convenção. Uma string numérica não implica, isoladamente, rejeição pelo banco; o risco está no formato e na conversão.

**Tratamento:**

Confirmar os formatos aceitos e registrar as conversões realizadas. Usar aritmética decimal para dinheiro. Não adivinhar formatos ambíguos nem arredondar sem uma regra definida.

---

### 8. Layout real do CSV diverge da documentação

**Observação:**

Em [`DOCUMENTACAO_TRIBUNAL.md`](DOCUMENTACAO_TRIBUNAL.md), linha 20, `tribunal.csv` é descrito como UTF-8, com campos separados por vírgula. Porém, o arquivo recebido usa ponto e vírgula, visível já no cabeçalho da linha 1 (`numero_processo;ente_devedor;valor_face`). Os bytes dos caracteres acentuados, como `í` em `Município` na linha 2, não formam UTF-8 válido e são compatíveis com Latin-1/Windows-1252; essa amostra não permite distinguir as duas codificações. Além disso, o valor `45.000,00`, na linha 2, usa ponto para milhares e vírgula decimal, contrariando o ponto decimal exigido na linha 26 do manual.

**Impacto direto:**

Um leitor configurado conforme o manual pode falhar ao decodificar o arquivo ou interpretar incorretamente suas colunas e valores, comprometendo toda a conciliação. A divergência entre o contrato documentado e o arquivo recebido é uma causa de falhas de leitura; rejeições silenciosas, descritas no item 3, podem ocultar suas consequências. O fluxo de abrir e salvar no Excel é um possível ponto de alteração do formato, mas os arquivos disponíveis não comprovam onde a divergência foi introduzida.

**Tratamento:**

Preservar o arquivo original e registrar a codificação, o separador e o formato monetário utilizados na leitura. Para esta amostra, usar uma configuração explícita compatível com os bytes recebidos, separador `;` e conversão monetária brasileira com aritmética decimal. Validar o cabeçalho, a quantidade de campos e os valores, registrando falhas sem descartá-las silenciosamente. Confirmar com o tribunal o leiaute oficial e versionar o contrato de integração; evitar que abrir e salvar no Excel seja uma etapa obrigatória antes da importação.

## Dúvidas para o tribunal e a operação

1. `evento_id` é único globalmente para o processo ou cada evento tem seu próprio?
2. Nos casos onde são repetidos os `evento_id`, qual deve prevalecer? Como ordenar e aplicar retificações?
3. Em questão de estornos, `ev-007` estorna `ev-006`. Mas na documentação do tribunal, não é aceito o estorno de um estorno. Como proceder em casos assim?
4. No evento `ev-004`, o valor é `null`. Qual deve ser o comportamento neste caso? E em casos como `ev-009` com valor negativo?
5. No caso do evento `ev-002`, o valor é uma string com vírgula. Qual deve ser o comportamento neste caso? Além disso, a data está no formato brasileiro (10/04/2025). Qual deve ser o comportamento neste caso? E no caso de repitição de dados identicos, qual deve ser o comportamento?
6. Qual é a origem oficial de `apresentacoes.json`? E qual é o formato da data deve ser utilizado?
7. Qual valor de face e ente devedor devem prevalecer nos conflitos entre as bases?
8. As listas representam a mesma carteira e data de referência? Existem filtros que expliquem as ausências?
9. Quais eventos já foram importados e quais falharam? Existem logs para reconstruir os totais?
10. Qual é o layout oficial de `tribunal.csv`: codificação, separador e formato monetário? O arquivo disponível é o recebido do tribunal ou já foi salvo pelo Excel? Existe uma cópia anterior para comparação?
