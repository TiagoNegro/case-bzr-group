# Limpeza e conciliação de precatórios

Script em Python para limpar os dados do case, comparar Tribunal e Hub e registrar correções, descartes e dúvidas. O diagnóstico está em [docs/DIAGNOSTICO.md](docs/DIAGNOSTICO.md) e as instruções do case em [docs/ENUNCIADO.md](docs/ENUNCIADO.md).

## Requisitos

- Python 3.10 ou superior.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) instalado.

O script usa a biblioteca padrão do Python e, no Windows, o pacote `tzdata` para consultar fusos IANA. Os testes usam pytest, instalado pelo uv como dependência de desenvolvimento.

## Como executar

Na raiz do projeto, crie o ambiente e instale as dependências:

```bash
uv venv
uv sync
```

Execute a limpeza e os testes:

```bash
uv run python limpeza.py
uv run pytest -v
```

Não é necessário ativar o ambiente virtual. O terminal mostra a tarefa em execução, as quantidades de registros limpos, descartados e pendentes e os caminhos das saídas.

### Diretórios de entrada e saída

Por padrão, o script lê os arquivos em `data/original/` e grava os resultados em `data/output/`. É possível mudar a pasta de entrada, a pasta de saída ou ambas, sem editar o código:

| Opção | Função |
|---|---|
| `--entrada` | Define a pasta que contém os quatro arquivos a analisar: `pagamentos.json`, `apresentacoes.json`, `tribunal.csv` e `hub.csv`. |
| `--saida` | Define a pasta em que serão gravadas as bases limpas, as pendências e os relatórios. A pasta é criada se não existir. |

Por exemplo, para analisar outro conjunto de arquivos guardado em `data/nova_remessa/` e salvar os resultados separadamente em `outputs/nova_remessa/`:

```bash
uv run python limpeza.py --entrada data/nova_remessa --saida outputs/nova_remessa
```

Antes de executar esse exemplo, coloque os quatro arquivos na pasta de entrada indicada. Os nomes dos arquivos devem ser mantidos.

Também é possível alterar apenas a saída e continuar lendo de `data/original/`:

```bash
uv run python limpeza.py --saida outputs
```

Ou alterar apenas a entrada, mantendo os resultados em `data/output/`:

```bash
uv run python limpeza.py --entrada data/nova_remessa
```

Caminhos relativos, como os dos exemplos, são interpretados a partir da pasta em que o comando é executado. Também podem ser usados caminhos absolutos. Entrada e saída devem ser pastas separadas, sem que uma esteja dentro da outra. Os originais são apenas lidos; os arquivos de resultado com os mesmos nomes são substituídos a cada execução na saída escolhida.

### Formato de datas de pagamento

A documentação exige datas como `2025-04-10`, mas `ev-002` contém `10/04/2025`. Para interpretar esse valor como **10 de abril de 2025**, informe a convenção dia/mês/ano:

```bash
uv run python limpeza.py --formato-data-br DD/MM/AAAA
```

Sem essa opção, pagamentos com datas nesse formato ficam em `pendencias.json`, pois o script não escolhe automaticamente entre dia/mês e mês/dia. Datas no formato `AAAA-MM-DD` não precisam dessa opção.

### Fuso horário e cálculo do prazo

Uma data de apresentação com horário representa um instante. Esse mesmo instante pode cair em dias diferentes dependendo do fuso. A opção `--fuso` define em qual fuso o script deve obter a data usada para calcular o prazo de pagamento e comparar as apresentações entre as fontes.

Por exemplo, o arquivo contém `2025-04-03T01:00:00Z`. O `Z` indica UTC. Em São Paulo, nesse instante, ainda eram **22h de 2 de abril de 2025**:

| Fuso usado no cálculo | Data considerada | Prazo pela regra do case |
|---|---|---|
| `UTC` | 03/04/2025 | 31/12/2027 |
| `America/Sao_Paulo` | 02/04/2025 | 31/12/2026 |

Isso acontece porque a regra do case inclui apresentações até 2 de abril no prazo do ano seguinte. Portanto, o fuso precisa representar a regra da operação, e não ser escolhido pelo prazo que se deseja obter.

Se a operação adotar o fuso de São Paulo, execute:

```bash
uv run python limpeza.py --fuso America/Sao_Paulo
```

Sem `--fuso`, apresentações com horário ficam pendentes; datas sem horário, como `2025-04-02`, são tratadas diretamente como datas civis. Informar o fuso não altera o horário original nem completa registros cujo horário veio sem indicação de fuso na origem.

As opções de formato brasileiro e fuso são independentes e podem ser combinadas. Exemplo usando ambas e gravando os resultados em `outputs/`:

```bash
uv run python limpeza.py --saida outputs --formato-data-br DD/MM/AAAA --fuso America/Sao_Paulo
```

O formato brasileiro e o fuso de negócio não foram definidos no contrato fornecido pelo tribunal. No case, essas escolhas podem ser usadas como hipóteses explícitas para explorar os resultados; antes de produção, precisam ser confirmadas com o tribunal ou a operação. A configuração utilizada fica registrada em `relatorio.json`. Essas opções não resolvem estornos, versões conflitantes ou divergências cadastrais.

## Arquivos gerados

Os arquivos abaixo são gravados em **`data/output/`** por padrão, ou na pasta informada em `--saida`:

| Arquivo | Conteúdo |
|---|---|
| `pagamentos_limpos.json` | Pagamentos válidos, sem repetições idênticas ou versões conflitantes. |
| `apresentacoes_limpas.json` | Apresentações válidas, com prazo de pagamento calculado pela regra do case. |
| `tribunal_limpo.csv` | Cadastros válidos do Tribunal, sem conflitos conhecidos de valor ou ente com o Hub. |
| `hub_limpo.csv` | Cadastros válidos do Hub, com prazos revisados e sem conflitos conhecidos de valor ou ente com o Tribunal. |
| `pendencias.json` | Registros que exigem revisão, acompanhados dos motivos. |
| `relatorio.md` | Relatório para leitura, dividido em Correções, Descartados, Avisos e Dúvidas, com itens numerados. |
| `relatorio.json` | Configuração da execução, contagens e ocorrências em formato estruturado. |

Os processos aparecem com máscara CNJ em todos os arquivos finais. A comparação interna usa somente os dígitos. Os valores monetários são tratados com precisão decimal e exportados com duas casas, como texto nos JSONs.

Repetições de um processo com conteúdo idêntico na mesma fonte são consolidadas, inclusive quando a única diferença é a máscara. Cadastros ou apresentações com conteúdos diferentes para o mesmo processo na mesma fonte ficam pendentes, sem escolher uma versão. Conflitos cadastrais internos também impedem publicar os cadastros correspondentes como conciliados.

As datas de apresentação do Hub e de `apresentacoes.json` também são comparadas por processo, usando a data civil no fuso configurado quando houver horário. Datas interpretáveis e divergentes deixam os registros envolvidos pendentes em ambas as fontes, mesmo que gerem o mesmo ano de prazo; nenhuma fonte é escolhida automaticamente. Datas que não puderem ser interpretadas continuam pendentes pela validação individual.

Registros com valores ou entes divergentes entre Tribunal e Hub ficam nas pendências, sem escolher automaticamente qual fonte está correta. Ausências em uma das fontes são sinalizadas no relatório. Estornos e versões diferentes do mesmo evento também aguardam confirmação.

Os arquivos originais e o SQL não são alterados. Cada execução substitui os resultados anteriores. Se houver erro de leitura ou estrutura, os resultados anteriores podem permanecer na pasta e não representam a execução que falhou. As bases limpas não representam um saldo financeiro definitivo nem autorizam automaticamente a importação no banco.

## Uso de IA e conferência

Após um diagnóstico inicial dos dados originais do case, foi feito um levantamento conjunto com o GPT-6.1 Sol para buscar outras pendências.

A implementação do script começou pelo planejamento, com solicitação de revisão do `ENUNCIADO.md` e do `DIAGNOSTICO.md`. As orientações incluíram preservar os arquivos preexistentes e manter o `limpeza.py` na raiz do projeto.

Foi solicitado o uso de TDD a partir das bases analisadas. Também foram fornecidos exemplos das mensagens esperadas no terminal, indicando a tarefa em execução e seus resultados, e exemplos de como deveriam ser os novos arquivos e o relatório.

Ao final da primeira rodada, o código e os arquivos gerados foram revisados. Foram necessários ajustes para chegar ao resultado desejado, incluindo a migração dos testes para pytest, a organização do relatório em blocos enumerados, a separação dos cadastros conflitantes e a exibição dos processos com máscara.

A conferência incluiu testes automatizados das regras críticas, comparação com os dados do case, verificação da preservação dos originais e do SQL, além da revisão das saídas geradas.
