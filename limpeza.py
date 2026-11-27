import argparse
from collections import defaultdict
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
import json
from pathlib import Path
import re
import tempfile
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Expressão regular para validar o formato do processo
MASCARA = r'[0-9]{7}-[0-9]{2}\.[0-9]{4}\.[0-9]\.[0-9]{2}\.[0-9]{4}'

# Caminho base do projeto
BASE = Path(__file__).resolve().parent


def valida_numero_processo(valor):
    if not isinstance(valor, str) or not re.fullmatch(r'[0-9]{20}|' + MASCARA, valor):
        raise ValueError('Processo ausente ou fora da estrutura CNJ; não completar dígitos.')
    return re.sub(r'[-.]', '', valor)


def mascarar_processo(valor):
    try:
        p = valida_numero_processo(valor)
    except ValueError:
        return f'Processo inválido: {valor!r}'
    return f'{p[:7]}-{p[7:9]}.{p[9:13]}.{p[13]}.{p[14:16]}.{p[16:]}'


def valida_moeda(valor):
    if valor is None or isinstance(valor, bool):
        raise ValueError('Valor incompatível: nulo ou booleano.')
    texto = str(valor)
    if re.fullmatch(r'(?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+),[0-9]{2}', texto):
        texto = texto.replace('.', '').replace(',', '.')
    try:
        numero = Decimal(texto)
        if not numero.is_finite() or numero <= 0 or numero != numero.quantize(Decimal('.01')):
            raise ValueError('Valor incompatível: deve ser positivo e ter precisão de centavos.')
        if numero >= Decimal('10000000000000'):
            raise ValueError('Valor incompatível com NUMERIC(15,2).')
        return numero.quantize(Decimal('.01'))
    except InvalidOperation as exc:
        raise ValueError('Valor incompatível: formato monetário inválido.') from exc


def valida_data_civil(texto):
    if not isinstance(texto, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', texto):
        raise ValueError('Data deve usar AAAA-MM-DD.')
    return date.fromisoformat(texto)


def data_apresentacao(texto, fuso=None):
    if isinstance(texto, str) and 'T' in texto:
        if not fuso:
            raise ValueError('Apresentação com horário exige confirmação do fuso de negócio.')
        momento = datetime.fromisoformat(texto.replace('Z', '+00:00'))
        if momento.tzinfo is None:
            raise ValueError('Apresentação com horário sem indicação de fuso na origem.')
        dia = momento.astimezone(ZoneInfo(fuso)).date()
    else:
        dia = valida_data_civil(texto)
    return dia


def valida_prazo(texto, fuso=None):
    dia = data_apresentacao(texto, fuso)
    ano = dia.year + (1 if (dia.month, dia.day) <= (4, 2) else 2)
    return date(ano, 12, 31).isoformat()


def normalizar_nome(texto):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', texto)
                            if not unicodedata.combining(c)).casefold().split())


def ler_fontes(entrada):
    fontes = {}
    for nome in ('pagamentos.json', 'apresentacoes.json'):
        try:
            registros = json.loads((entrada / nome).read_text(encoding='utf-8'), parse_float=Decimal,
                                   parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f'Número JSON inválido: {s}')))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'{nome}: JSON ou codificação inválida: {exc}') from exc
        if not isinstance(registros, list) or any(not isinstance(r, dict) for r in registros):
            raise ValueError(f'{nome}: esperado uma lista de objetos.')
        fontes[nome] = [(i, r) for i, r in enumerate(registros, 1)]
    especificacoes = {
        'tribunal.csv': ('cp1252', ';', ['numero_processo', 'ente_devedor', 'valor_face']),
        'hub.csv': ('utf-8', ',', ['processo', 'ente', 'valor_face', 'apresentado_em', 'prazo_pagamento']),
    }
    for nome, (encoding, separador, campos) in especificacoes.items():
        try:
            leitor = csv.DictReader(io.StringIO((entrada / nome).read_text(encoding=encoding)),
                                    delimiter=separador, strict=True)
            if leitor.fieldnames != campos:
                raise ValueError(f'{nome}: cabeçalho incompatível com o leiaute esperado.')
            linhas = []
            for registro in leitor:
                if None in registro or any(v is None for v in registro.values()):
                    raise ValueError(f'{nome}: quantidade de campos inválida na linha {leitor.line_num}.')
                linhas.append((leitor.line_num, registro))
            fontes[nome] = linhas
        except (UnicodeError, csv.Error) as exc:
            raise ValueError(f'{nome}: falha na leitura: {exc}') from exc
    return fontes


def executar(entrada=BASE / 'data/original', saida=BASE / 'data/output', formato_br=False, fuso=None):
    entrada, saida = Path(entrada).resolve(), Path(saida).resolve()
    if saida == entrada or entrada in saida.parents or saida in entrada.parents:
        raise ValueError('Entrada e saída não podem se sobrepor.')
    if fuso:
        ZoneInfo(fuso)
    # Toda a estrutura é lida antes de publicar qualquer resultado.
    fontes = ler_fontes(entrada)
    ocorrencias, pendencias, resultados = [], [], {}
    resumo = {n: dict(recebidos=len(rs), limpos=0, pendentes=0, descartados=0, correcoes=0)
              for n, rs in fontes.items()}

    def registrar(categoria, fonte, posicao, registro, motivo, antes=None, depois=None):
        ocorrencias.append(dict(categoria=categoria, fonte=fonte,
                                localizacao=f'{"registro" if fonte.endswith("json") else "linha"} {posicao}',
                                processo=(mascarar_processo(registro.get('processo', registro.get('numero_processo', '')))
                                          if registro else 'Arquivo completo'),
                                evento_id=registro.get('evento_id'), motivo=motivo,
                                antes=antes, depois=depois))
        if categoria == 'corrigido':
            resumo[fonte]['correcoes'] += 1

    def pendente(fonte, posicao, registro, motivos):
        resumo[fonte]['pendentes'] += 1
        pendencias.append(dict(fonte=fonte, posicao=posicao, original=registro, motivos=motivos))
        for motivo in motivos:
            registrar('duvida', fonte, posicao, registro, motivo)

    def normalizar(fonte, posicao, registro, campo):
        novo = dict(registro)
        novo[campo] = valida_numero_processo(registro.get(campo))
        if novo[campo] != registro[campo]:
            # Relatórios usam máscara até ao descrever a normalização.
            registrar('corrigido', fonte, posicao, registro, 'Processo normalizado para comparação interna; máscara mantida na saída e zeros iniciais preservados.',
                      mascarar_processo(registro[campo]), mascarar_processo(novo[campo]))
        return novo

    repeticoes_cadastrais = set()
    for fonte, registros in fontes.items():
        print(f'Verificação de {fonte}...')
        limpos = []
        if fonte == 'tribunal.csv':
            registrar('aviso', fonte, 1, {},
                      'Leiaute recebido: Windows-1252 (bytes também compatíveis com Latin-1), separador ; '
                      'e moeda brasileira; difere de UTF-8, vírgula e ponto decimal documentados.')
        grupos = defaultdict(list)
        if fonte == 'pagamentos.json':
            for pos, registro in registros:
                if isinstance(registro.get('evento_id'), str) and registro['evento_id'].strip():
                    grupos[registro['evento_id']].append(registro)
        campo = 'numero_processo' if fonte == 'tribunal.csv' else 'processo'
        conflitos_locais = set()
        repeticoes_locais = set()
        if fonte != 'pagamentos.json':
            por_processo = defaultdict(list)
            for pos, registro in registros:
                try:
                    chave = valida_numero_processo(registro.get(campo))
                except ValueError:
                    continue  # A validação individual registrará o problema.
                comparavel = {**registro, campo: chave}
                por_processo[chave].append((pos, comparavel))
            for grupo in por_processo.values():
                if any(r != grupo[0][1] for _, r in grupo[1:]):
                    conflitos_locais.update(pos for pos, _ in grupo)
                else:
                    repeticoes_locais.update(pos for pos, _ in grupo[1:])
        vistos = set()
        for pos, registro in registros:
            if pos in conflitos_locais:
                pendente(fonte, pos, registro, [
                    'Mesmo processo possui conteúdos diferentes na fonte; nenhuma versão escolhida.'
                ])
                continue
            if pos in repeticoes_locais:
                repeticoes_cadastrais.add((fonte, pos))
                resumo[fonte]['descartados'] += 1
                registrar('descartado', fonte, pos, registro,
                          'Repetição do mesmo processo com conteúdo idêntico; primeira ocorrência preservada para validação.')
                continue
            if fonte == 'pagamentos.json':
                evento = registro.get('evento_id')
                grupo = grupos.get(evento, []) if isinstance(evento, str) else []
                if grupo and any(r != grupo[0] for r in grupo[1:]):
                    pendente(fonte, pos, registro, ['Mesmo evento_id possui versões diferentes; regra de retificação não confirmada.'])
                    continue
                if grupo and evento in vistos:
                    resumo[fonte]['descartados'] += 1
                    registrar('descartado', fonte, pos, registro, 'Repetição idêntica do evento; primeira ocorrência preservada para validação.')
                    continue
                if grupo:
                    vistos.add(evento)
            campo = 'numero_processo' if fonte == 'tribunal.csv' else 'processo'
            motivos = []
            try:
                novo = normalizar(fonte, pos, registro, campo)
            except ValueError as exc:
                novo = dict(registro)
                motivos.append(str(exc))
            if fonte in ('pagamentos.json', 'tribunal.csv', 'hub.csv'):
                campo_valor = 'valor' if fonte == 'pagamentos.json' else 'valor_face'
                try:
                    novo[campo_valor] = str(valida_moeda(registro.get(campo_valor)))
                    if isinstance(registro.get(campo_valor), str) and ',' in registro[campo_valor]:
                        registrar('corrigido', fonte, pos, registro, 'Valor monetário brasileiro convertido para decimal exato.',
                                  registro[campo_valor], novo[campo_valor])
                except ValueError as exc:
                    motivos.append(str(exc))
            if fonte == 'pagamentos.json':
                if not isinstance(evento, str) or not evento.strip():
                    motivos.append('evento_id ausente ou inválido.')
                if registro.get('tipo') != 'pagamento':
                    motivos.append('Estorno exige regra e vínculo confirmados.' if registro.get('tipo') == 'estorno'
                                   else 'Tipo de evento desconhecido.')
                try:
                    texto = registro.get('data_pagamento')
                    if isinstance(texto, str) and re.fullmatch(r'[0-9]{2}/[0-9]{2}/[0-9]{4}', texto):
                        if not formato_br:
                            raise ValueError('Data brasileira ambígua; exige --formato-data-br DD/MM/AAAA.')
                        novo['data_pagamento'] = datetime.strptime(texto, '%d/%m/%Y').date().isoformat()
                        registrar('corrigido', fonte, pos, registro, 'Data brasileira convertida mediante configuração explícita.',
                                  texto, novo['data_pagamento'])
                    else:
                        valida_data_civil(texto)
                except ValueError as exc:
                    motivos.append(str(exc))
                try:
                    atualizado = registro.get('atualizado_em')
                    if not isinstance(atualizado, str) or 'T' not in atualizado:
                        raise ValueError('atualizado_em deve conter data e hora com fuso.')
                    if datetime.fromisoformat(atualizado.replace('Z', '+00:00')).tzinfo is None:
                        raise ValueError('atualizado_em sem fuso.')
                except ValueError as exc:
                    motivos.append(str(exc))
            if fonte in ('hub.csv', 'apresentacoes.json'):
                try:
                    calculado = valida_prazo(registro.get('apresentado_em'), fuso)
                    novo['prazo_pagamento'] = calculado
                    anterior = registro.get('prazo_pagamento')
                    if anterior and anterior != calculado:
                        registrar('corrigido', fonte, pos, registro, 'Prazo recalculado pela regra inclusiva de 2 de abril do case.',
                                  anterior, calculado)
                except (ValueError, TypeError, OverflowError) as exc:
                    motivos.append(f'Prazo pendente: {exc}')
            if fonte in ('tribunal.csv', 'hub.csv'):
                ente = registro.get('ente_devedor' if fonte == 'tribunal.csv' else 'ente')
                if not isinstance(ente, str) or not ente.strip():
                    motivos.append('Ente devedor ausente.')
            if motivos:
                pendente(fonte, pos, registro, motivos)
            else:
                limpos.append(novo)
        resultados[fonte] = limpos
        resumo[fonte]['limpos'] = len(limpos)
        s = resumo[fonte]
        incompativeis = sum(o['fonte'] == fonte and o['motivo'].startswith('Valor incompatível') for o in ocorrencias)
        print(f"Concluída: {s['limpos']} registros limpos, {s['descartados']} duplicatas descartadas, "
              f"{s['pendentes']} registros pendentes, {incompativeis} valores incompatíveis e {s['correcoes']} correções.")

    print('Conciliação de tribunal.csv e hub.csv...')
    # Inclui registros com pendências em outros campos, evitando falsas ausências.
    indices = {}
    for fonte, campo in [('tribunal.csv', 'numero_processo'), ('hub.csv', 'processo')]:
        indice = defaultdict(list)
        for pos, r in fontes[fonte]:
            if (fonte, pos) in repeticoes_cadastrais:
                continue
            try:
                indice[valida_numero_processo(r.get(campo))].append((pos, r))
            except ValueError:
                pass
        indices[fonte] = indice
    tribunal, hub = indices['tribunal.csv'], indices['hub.csv']
    conflitos = set()
    for chave in sorted(tribunal.keys() | hub.keys()):
        for fonte, indice in indices.items():
            if len(indice.get(chave, [])) > 1:
                conflitos.add(chave)
                for pos, r in indice[chave]:
                    registrar('duvida', fonte, pos, r, 'Processo repetido na fonte; conciliação sem escolha automática.')
        if chave not in tribunal or chave not in hub:
            fonte = 'hub.csv' if chave not in tribunal else 'tribunal.csv'
            ausente = 'tribunal.csv' if chave not in tribunal else 'hub.csv'
            pos, r = indices[fonte][chave][0]
            registrar('duvida', fonte, pos, r, f'Processo presente em {fonte}, ausente em {ausente}; confirmar escopo da carteira.')
        elif len(tribunal[chave]) == len(hub[chave]) == 1:
            pos_t, t = tribunal[chave][0]
            pos_h, h = hub[chave][0]
            referencia = f'tribunal.csv linha {pos_t} versus hub.csv linha {pos_h}'
            try:
                vt, vh = valida_moeda(t['valor_face']), valida_moeda(h['valor_face'])
                if vt != vh:
                    conflitos.add(chave)
                    def reais(v):
                        return 'R$ ' + f'{v:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
                    registrar('duvida', 'tribunal.csv', pos_t, t, f'Valor de face divergente: {referencia}.', reais(vt), reais(vh))
            except ValueError:
                pass  # Já registrado na validação individual.
            if normalizar_nome(t['ente_devedor']) != normalizar_nome(h['ente']):
                conflitos.add(chave)
                registrar('duvida', 'tribunal.csv', pos_t, t, f'Ente devedor divergente: {referencia}.', t['ente_devedor'], h['ente'])
    for fonte, campo in [('tribunal.csv', 'numero_processo'), ('hub.csv', 'processo')]:
        for chave in sorted(conflitos):
            motivo = 'Conflito cadastral entre Tribunal e Hub; registro separado até confirmação de valor e ente.'
            for pos, original in indices[fonte][chave]:
                existente = next((p for p in pendencias if p['fonte'] == fonte and p['posicao'] == pos), None)
                if existente is not None:
                    existente['motivos'].append(motivo)
                else:
                    pendente(fonte, pos, original, [motivo])
        resultados[fonte] = [r for r in resultados[fonte] if r[campo] not in conflitos]
        resumo[fonte]['limpos'] = len(resultados[fonte])
        print(f"Resultado final de {fonte}: {resumo[fonte]['limpos']} registros limpos, "
              f"{resumo[fonte]['pendentes']} registros pendentes após conciliação.")
    print('Conciliação das datas de apresentação entre Hub e apresentações...')
    datas_por_fonte = {}
    for fonte in ('hub.csv', 'apresentacoes.json'):
        indice = defaultdict(list)
        for pos, original in fontes[fonte]:
            if (fonte, pos) in repeticoes_cadastrais:
                continue
            try:
                chave = valida_numero_processo(original.get('processo'))
                dia = data_apresentacao(original.get('apresentado_em'), fuso)
            except (ValueError, TypeError, OverflowError):
                continue  # Pendências de interpretação já foram registradas.
            indice[chave].append((pos, original, dia))
        datas_por_fonte[fonte] = indice
    conflitos_datas = set()
    for chave in sorted(datas_por_fonte['hub.csv'].keys() & datas_por_fonte['apresentacoes.json'].keys()):
        grupo = [(fonte, pos, original, dia)
                 for fonte, indice in datas_por_fonte.items()
                 for pos, original, dia in indice[chave]]
        if len({dia for _, _, _, dia in grupo}) <= 1:
            continue
        conflitos_datas.add(chave)
        evidencia = '; '.join(f'{fonte} registro/linha {pos}: {dia.isoformat()}'
                              for fonte, pos, _, dia in grupo)
        motivo = f'Data de apresentação divergente entre Hub e apresentações: {evidencia}. Confirmar a fonte correta.'
        for fonte, pos, original, _ in grupo:
            existente = next((p for p in pendencias if p['fonte'] == fonte and p['posicao'] == pos), None)
            if existente is None:
                pendente(fonte, pos, original, [motivo])
            else:
                existente['motivos'].append(motivo)
                registrar('duvida', fonte, pos, original, motivo)
    for fonte in ('hub.csv', 'apresentacoes.json'):
        resultados[fonte] = [r for r in resultados[fonte] if r['processo'] not in conflitos_datas]
        resumo[fonte]['limpos'] = len(resultados[fonte])
        print(f"Após conferir apresentações, {fonte}: {resumo[fonte]['limpos']} registros limpos, "
              f"{resumo[fonte]['pendentes']} registros pendentes.")

    relatorio = dict(configuracao=dict(formato_data_br='DD/MM/AAAA' if formato_br else None, fuso=fuso),
                     fontes=resumo, ocorrencias=ocorrencias)
    publicar(saida, resultados, pendencias, relatorio)
    duvidas = sum(o['categoria'] == 'duvida' for o in ocorrencias)
    print(f'Finalizado com {duvidas} ocorrências pendentes de revisão. Não foi calculado saldo financeiro definitivo.')
    print(f'Relatórios: {saida / "relatorio.md"} e {saida / "relatorio.json"}')
    return relatorio


def publicar(saida, resultados, pendencias, relatorio):
    def para_exibicao(valor):
        if isinstance(valor, list):
            return [para_exibicao(item) for item in valor]
        if isinstance(valor, dict):
            novo = {}
            for chave, item in valor.items():
                if chave in ('processo', 'numero_processo'):
                    try:
                        item = mascarar_processo(valida_numero_processo(item))
                    except ValueError:
                        pass  # Preservar entradas inválidas, sem inventar uma máscara.
                novo[chave] = para_exibicao(item)
            return novo
        return valor

    def json_texto(valor):
        return json.dumps(para_exibicao(valor), ensure_ascii=False, indent=2, default=str) + '\n'

    arquivos = {
        'pagamentos_limpos.json': json_texto(resultados['pagamentos.json']),
        'apresentacoes_limpas.json': json_texto(resultados['apresentacoes.json']),
        'pendencias.json': json_texto(pendencias),
        'relatorio.json': json_texto(relatorio),
    }
    for fonte, destino, campos in [
        ('tribunal.csv', 'tribunal_limpo.csv', ['numero_processo', 'ente_devedor', 'valor_face']),
        ('hub.csv', 'hub_limpo.csv', ['processo', 'ente', 'valor_face', 'apresentado_em', 'prazo_pagamento']),
    ]:
        buffer = io.StringIO(newline='')
        escritor = csv.DictWriter(buffer, fieldnames=campos, lineterminator='\n')
        escritor.writeheader()
        escritor.writerows(para_exibicao(resultados[fonte]))
        arquivos[destino] = buffer.getvalue()
    linhas = ['# Relatório de limpeza', '',
              'Bases limpas passaram pela validação individual; conflitos de valor ou ente entre fontes '
              'foram separados nas pendências, sem escolher uma fonte como correta. Ausências continuam sinalizadas. '
              'Os totais são contagens desta amostra, não um saldo financeiro ou histórico completo.', '',
              '## Configuração', '', json_texto(relatorio['configuracao']).strip(), '',
              '## Resumo por fonte', '',
              '| Fonte | Recebidos | Limpos | Pendentes | Descartados | Correções |',
              '|---|---:|---:|---:|---:|---:|']
    for fonte, s in relatorio['fontes'].items():
        linhas.append(f"| {fonte} | {s['recebidos']} | {s['limpos']} | {s['pendentes']} | {s['descartados']} | {s['correcoes']} |")
    linhas += ['', 'Recebidos = limpos + pendentes + descartados. Correções contam ações, inclusive em registros '
               'que continuam pendentes. Uma pendência pode ter vários motivos.']
    for categoria, titulo in [('corrigido', 'Correções'), ('descartado', 'Descartados'),
                              ('aviso', 'Avisos'), ('duvida', 'Dúvidas')]:
        linhas += ['', f'## {titulo}', '']
        grupo = [o for o in relatorio['ocorrencias'] if o['categoria'] == categoria]
        if not grupo:
            linhas.append('Nenhuma ocorrência.')
        for numero, o in enumerate(grupo, 1):
            item = (f"{numero}. {o['fonte']}, {o['localizacao']}; {o['processo']}"
                    + (f"; evento {o['evento_id']}" if o['evento_id'] else '') + f". {o['motivo']}")
            if o['antes'] is not None or o['depois'] is not None:
                if 'divergente:' in o['motivo']:
                    item += f" Tribunal: {o['antes']}; Hub: {o['depois']}."
                elif o['antes'] != o['depois']:
                    item += f" Antes: {o['antes']}; depois: {o['depois']}."
            linhas.append(item)
    arquivos['relatorio.md'] = '\n'.join(linhas) + '\n'
    saida.mkdir(parents=True, exist_ok=True)
    # Preparar todos os arquivos antes de substituir os resultados anteriores.
    with tempfile.TemporaryDirectory(dir=saida) as temporario:
        for nome, texto in arquivos.items():
            Path(temporario, nome).write_text(texto, encoding='utf-8')
        for nome in arquivos:
            Path(temporario, nome).replace(saida / nome)
            print(f'Arquivo gerado: {saida / nome}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entrada', type=Path, default=BASE / 'data/original')
    parser.add_argument('--saida', type=Path, default=BASE / 'data/output')
    parser.add_argument('--formato-data-br', choices=['DD/MM/AAAA'])
    parser.add_argument('--fuso', help='Fuso IANA confirmado pela operação, por exemplo America/Sao_Paulo')
    args = parser.parse_args()
    try:
        executar(args.entrada, args.saida, bool(args.formato_data_br), args.fuso)
    except (ValueError, OSError, ZoneInfoNotFoundError) as exc:
        parser.exit(1, f'Falha na limpeza: {exc}\nResultados anteriores, se existentes, não representam esta execução.\n')


if __name__ == '__main__':
    main()
