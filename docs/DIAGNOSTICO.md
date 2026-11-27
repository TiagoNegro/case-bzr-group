# Diagnóstico do Projeto

## Fluxo Operacional

Em análise do fluxo operacional pelos arquivos: [`apresentacoes.json`](../data/original/apresentacoes.json), [`pagamentos.json`](../data/original/pagamentos.json), [`tribunal.csv`](../data/original/tribunal.csv) e [`hub.csv`](../data/original/hub.csv), foram encontradas as seguintes divergências com base em sua criticidade em ordem:

---
### 1. Importação aditiva sem proteção contra repetição e eventos com conteúdo diferente tendo um mesmo identificador
**Observação:**
Com base no [`ENUNCIADO.md`](ENUNCIADO.md), o fluxo descrito apresenta risco de contabilização repetida, e o esquema do banco [`schema.sql`](../docs/schema.sql) não possui uma restrição que impeça eventos duplicados. Como exemplo, no arquivo [`pagamentos.json`](../data/original/pagamentos.json), o evento `ev-002` nas linhas 11-16 e 27-32, o que pode acrescentar R$ 2.500,50 indevidamente, por apresentar conteúdo idêntico, enquanto o evento `ev-001` nas linhas 3-8 e 67-72, possui valores e horários de atualização diferentes, para um mesmo evento.

**Impacto direto:**
A repetição dos eventos, combinada com o processamento aditivo sem proteção suficiente, pode gerar um total pago maior que o real. A ausência da restrição de unicidade para o campo `evento_id` no banco permite que a duplicidade seja persistida.

**Tratamento:**
Ignorar repetições idênticas e sinalizar conteúdos diferentes para o mesmo identificador até confirmar a regra de atualização com o tribunal.
Fazer uma mudança no schema.sql para adicionar uma restrição de unicidade para o campo `evento_id`, após confirmação do escopo em conjunto com o tribunal.

Adição direta no schema.sql:
```sql
evento_id TEXT NOT NULL UNIQUE
```
Ou adição de uma CONSTRAINT para garantir a unicidade do evento_id.
```sql
ALTER TABLE pagamento
    ADD CONSTRAINT pagamento_evento_unico UNIQUE (evento_id);
```

---
### 2. Estorno contradiz a documentação do tribunal
**Observação:**
Em [`pagamentos.json`](../data/original/pagamentos.json), linhas 51-56, o evento `ev-006` é um pagamento de R$ 5.000,00 e, nas linhas 59-64, o evento `ev-007` é um estorno de R$ 5.000,00 no mesmo processo, no dia seguinte. A documentação do tribunal, em [`DOCUMENTACAO_TRIBUNAL.md`](../docs/DOCUMENTACAO_TRIBUNAL.md), linha 15, diz que o tipo é sempre `pagamento`, no banco [`schema.sql`](../docs/schema.sql), linha 12, é aceito `estorno`. Mas como há divergência entre a documentação e os dados recebidos, é necessário confirmar a regra de processamento dos estornos e se existe vínculo com o pagamento original.

**Impacto direto:**
Se o estorno for ignorado, e sua representação for a de reversão do pagamento, isso manterá o total pago incorreto em R$ 5.000,00. Se o importador somar ambos os eventos, contabilizará R$ 10.000,00 indevidamente.

**Tratamento:**
Confirmar a documentação em conjunto ao Tribunal para definir seu procedimento, se o tipo `estorno` deve ser aceito, pois o seu tipo já é permitido pelo esquema do banco e efetuar a subtração do valor do estorno do total pago. Caso venha a ser o estorno venha a ser a reversão, subtrair seu valor do total pago. Até o esclarecimento preservar o evento como pendente e registrar o motivo.

---

### 3. Falhas ocultas na importação
**Observação:**
Em [`ENUNCIADO.md`](ENUNCIADO.md), linha 19, eventos não importados são ignorados sem aviso. Há eventos incompativeis com as restrições do bnanco ou sem informação suficientes para importação: `ev-004` linhas 35-40 com valor nulo, `ev-009` linhas 83-88 com valor negativo e `ev-010` linhas 91-96 sem processo. Ausência explícita de comunicação de rejeições, amplifica as consequências de problemas de dados e contrato.

**Impacto direto:**
Não é possível afirmar quais registros o script efetivamente rejeitou sem seus logs.
Saldo incompleto sem indicação da perda, impossibilidade de conciliar recebidos versus processados e dificuldade de corrigir ou reprocessar os rejeitados.

**Tratamento:**
Retornar os registros rejeitados com motivo específico e contagens por resultado.

---

### 4. Representação de processo incompatível com cadastro e conciliação
**Observação:**
Em [`schema.sql`](../docs/schema.sql), linha 3, o campo `numero_processo` é definido como `CHAR(20) NOT NULL UNIQUE`, o que implica que o número do processo comporta até 20 caracteres, e a definição também não garente que o conteúdo seja apenas numérico. No entanto, em [`pagamentos.json`](../data/original/pagamentos.json), em sua maioria, os processos possuem máscara de formatação (ex: `0023456-21.2020.8.26.0053`), o que pode causar incompatibilidade ao inserir no banco.

**Impacto direto:**
Se os processos forem inseridos com a máscara, o banco não permitirá a inserção devido à restrição de tamanho, pois o número do processo terá mais de 20 caracteres.

**Tratamento:**
Realizar o tratamento da máscara do número do processo antes de inserir no banco, garantindo que o número do processo venha comportar os 20 caracteres, preservervando os zeros iniciais.

---

### 5. Comparação da lista do tribunal com hub
**Observação:**
Em [`tribunal.csv`](../data/original/tribunal.csv) e [`hub.csv`](../data/original/hub.csv), há processos que não possuem correspondência entre si, o que pode causar incompatibilidade ao inserir no banco.

**Impacto direto:**
Se os processos não possuem correspondência, o banco não permitirá a inserção devido à restrição de tamanho, pois o número do processo terá mais de 20 caracteres.

**Tratamento:**
Realizar o tratamento da máscara do número do processo antes de inserir no banco, garantindo que o número do processo venha comportar os 20 caracteres, preservervando os zeros iniciais.

---
