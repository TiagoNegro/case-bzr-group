import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import pytest
from limpeza import executar, mascarar_processo, valida_moeda, valida_numero_processo, valida_prazo
ROOT = Path(__file__).resolve().parents[1]
CNJ = '0012345-10.2019.8.26.0053'

class TestRegras:

    def test_processo_preserva_zeros_e_rejeita_dano(self):
        assert valida_numero_processo(CNJ) == '00123451020198260053'
        assert valida_numero_processo(valida_numero_processo(CNJ)) == valida_numero_processo(CNJ)
        for valor in ('', '123', 'abc' + valida_numero_processo(CNJ), 123, '１' * 20):
            with pytest.raises(ValueError):
                valida_numero_processo(valor)

    def test_dinheiro_exato(self):
        assert str(valida_moeda('2.500,50')) == '2500.50'
        assert str(valida_moeda('0.10') + valida_moeda('0.20')) == '0.30'
        for valor in (None, '-200', '0', '1.001', True, 'NaN', 'Infinity'):
            with pytest.raises(ValueError):
                valida_moeda(valor)

    def test_corte_inclusivo_e_bissexto(self):
        for data, esperado in [('2025-04-01', '2026-12-31'), ('2025-04-02', '2026-12-31'), ('2025-04-03', '2027-12-31'), ('2024-02-29', '2025-12-31')]:
            assert valida_prazo(data) == esperado
        with pytest.raises(ValueError):
            valida_prazo('2025-02-29')

    def test_fuso_explicito(self):
        with pytest.raises(ValueError):
            valida_prazo('2025-04-03T01:00:00Z')
        assert valida_prazo('2025-04-03T01:00:00Z', 'America/Sao_Paulo') == '2026-12-31'
        assert valida_prazo('2025-04-03T01:00:00Z', 'UTC') == '2027-12-31'

class TestIntegracao:

    def test_conflitos_de_cadastro_ficam_fora_das_bases_limpas(self):
        resultado, terminal = self.rodar()
        conflitantes = {valida_numero_processo('0023456-21.2020.8.26.0053'), valida_numero_processo('0045678-43.2021.5.02.0011')}
        for arquivo, campo in [('hub_limpo.csv', 'processo'), ('tribunal_limpo.csv', 'numero_processo')]:
            with (self.saida / arquivo).open(encoding='utf-8', newline='') as entrada:
                assert not any(valida_numero_processo(r[campo]) in conflitantes for r in csv.DictReader(entrada))
        pendencias = json.loads((self.saida / 'pendencias.json').read_bytes())
        for fonte in ('hub.csv', 'tribunal.csv'):
            registros = [p for p in pendencias if p['fonte'] == fonte]
            campo = 'processo' if fonte == 'hub.csv' else 'numero_processo'
            for chave in conflitantes:
                encontrados = [p for p in registros if valida_numero_processo(p['original'][campo]) == chave]
                assert len(encontrados) == 1
                assert any('Conflito cadastral' in motivo for motivo in encontrados[0]['motivos'])
        assert resultado['fontes']['hub.csv']['limpos'] == 2
        assert resultado['fontes']['tribunal.csv']['limpos'] == 3
        assert 'Resultado final de hub.csv: 2 registros limpos' in terminal

    @pytest.fixture(autouse=True)
    def preparar_fontes(self, tmp_path):
        self.entrada = tmp_path / 'original'
        shutil.copytree(ROOT / 'data/original', self.entrada)
        self.saida = tmp_path / 'output'

    def rodar(self, **kwargs):
        terminal = io.StringIO()
        with contextlib.redirect_stdout(terminal):
            resultado = executar(self.entrada, self.saida, **kwargs)
        return (resultado, terminal.getvalue())

    def test_amostra_conservadora_auditoria_e_reexecucao(self):
        protegidos = [*self.entrada.iterdir(), ROOT / 'docs/schema.sql']
        antes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protegidos}
        resultado, terminal = self.rodar()
        pagamentos = json.loads((self.saida / 'pagamentos_limpos.json').read_bytes())
        assert [p['evento_id'] for p in pagamentos] == ['ev-003', 'ev-005', 'ev-006', 'ev-008', 'ev-011']
        assert resultado['fontes']['pagamentos.json']['descartados'] == 1
        assert resultado['fontes']['pagamentos.json']['pendentes'] == 7
        assert len(json.loads((self.saida / 'apresentacoes_limpas.json').read_bytes())) == 4
        relatorio = (self.saida / 'relatorio.md').read_text(encoding='utf-8')
        for trecho in ('30.000,00', '31.000,00', 'União', 'Município de Santos', '0056789-54.2017.4.03.6100', '0067890-65.2016.8.26.0053', '2026-12-31', 'Windows-1252'):
            assert trecho in relatorio
        assert valida_numero_processo(CNJ) not in relatorio
        assert 'Verificação de pagamentos.json' in terminal
        assert 'pendentes' in terminal
        conteudo = {p.name: p.read_bytes() for p in self.saida.iterdir()}
        self.rodar()
        assert conteudo == {p.name: p.read_bytes() for p in self.saida.iterdir()}
        assert antes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protegidos}

    def test_parametros_liberam_data_e_fuso_sem_resolver_conflitos(self):
        self.rodar(formato_br=True, fuso='America/Sao_Paulo')
        pagamentos = json.loads((self.saida / 'pagamentos_limpos.json').read_bytes())
        evento = next((p for p in pagamentos if p['evento_id'] == 'ev-002'))
        assert (evento['valor'], evento['data_pagamento']) == ('2500.50', '2025-04-10')
        assert not any((p['evento_id'] in ('ev-001', 'ev-007') for p in pagamentos))
        assert len(json.loads((self.saida / 'apresentacoes_limpas.json').read_bytes())) == 6

    def test_arquivo_invalido_nao_publica(self):
        (self.entrada / 'pagamentos.json').write_text('{')
        with pytest.raises(ValueError):
            self.rodar()
        assert not self.saida.exists()

    def test_saida_nao_pode_ser_original(self):
        self.saida = self.entrada
        with pytest.raises(ValueError):
            self.rodar()

    def test_relatorio_json_mascarado_e_contagens_fecham(self):
        resultado, _ = self.rodar()
        for resumo in resultado['fontes'].values():
            assert resumo['recebidos'] == resumo['limpos'] + resumo['pendentes'] + resumo['descartados']
        assert valida_numero_processo(CNJ) not in (self.saida / 'relatorio.json').read_text(encoding='utf-8')
        divergencias = [o for o in resultado['ocorrencias'] if 'Ente devedor divergente' in o['motivo']]
        assert len(divergencias) == 1
        assert divergencias[0]['antes'] == 'União'

    def test_csv_invalido_preserva_resultados_anteriores(self):
        self.rodar()
        antes = {p.name: p.read_bytes() for p in self.saida.iterdir()}
        with (self.entrada / 'hub.csv').open('a') as arquivo:
            arquivo.write('apenas,dois\n')
        with pytest.raises(ValueError, match='quantidade de campos'):
            self.rodar()
        assert antes == {p.name: p.read_bytes() for p in self.saida.iterdir()}

    def test_cabecalho_invalido_nao_publica(self):
        arquivo = self.entrada / 'tribunal.csv'
        arquivo.write_bytes(arquivo.read_bytes().replace(b'numero_processo', b'processo'))
        with pytest.raises(ValueError, match='cabeçalho'):
            self.rodar()
        assert not self.saida.exists()

    def test_processos_validos_mascarados_em_todos_os_arquivos_finais(self):
        self.rodar()

        def verificar(valor):
            if isinstance(valor, list):
                for item in valor:
                    verificar(item)
            elif isinstance(valor, dict):
                for chave, item in valor.items():
                    if chave in ('processo', 'numero_processo'):
                        try:
                            canonico = valida_numero_processo(item)
                        except ValueError:
                            continue
                        assert item == mascarar_processo(canonico)
                    verificar(item)

        for arquivo in self.saida.glob('*.json'):
            verificar(json.loads(arquivo.read_bytes()))
        for arquivo in self.saida.glob('*.csv'):
            with arquivo.open(encoding='utf-8', newline='') as entrada:
                verificar(list(csv.DictReader(entrada)))

    @pytest.mark.parametrize('fonte', ['tribunal.csv', 'hub.csv', 'apresentacoes.json'])
    @pytest.mark.parametrize('conflitante', [False, True])
    def test_repeticoes_e_conflitos_por_processo(self, fonte, conflitante):
        arquivo = self.entrada / fonte
        if fonte.endswith('.json'):
            registros = json.loads(arquivo.read_bytes())
            novo = dict(registros[0])
            if conflitante:
                novo['apresentado_em'] = '2025-04-03'
            registros.append(novo)
            arquivo.write_text(json.dumps(registros))
        else:
            encoding = 'cp1252' if fonte == 'tribunal.csv' else 'utf-8'
            texto = arquivo.read_text(encoding=encoding)
            linha = texto.splitlines()[1]
            if conflitante:
                linha = linha.replace('45.000,00', '46.000,00').replace('45000.00', '46000.00')
            arquivo.write_text(texto.rstrip('\n') + '\n' + linha + '\n', encoding=encoding)
        relatorio, _ = self.rodar()
        if fonte.endswith('.json'):
            limpos = json.loads((self.saida / 'apresentacoes_limpas.json').read_bytes())
        else:
            destino = 'tribunal_limpo.csv' if fonte == 'tribunal.csv' else 'hub_limpo.csv'
            with (self.saida / destino).open(encoding='utf-8', newline='') as f:
                limpos = list(csv.DictReader(f))
        campo = 'numero_processo' if fonte == 'tribunal.csv' else 'processo'
        encontrados = [r for r in limpos if r[campo] == CNJ]
        assert len(encontrados) == (0 if conflitante else 1)
        if conflitante:
            pendencias = json.loads((self.saida / 'pendencias.json').read_bytes())
            afetados = [p for p in pendencias if p['fonte'] == fonte and p['original'][campo] == CNJ]
            assert len(afetados) == 2
        else:
            assert relatorio['fontes'][fonte]['descartados'] == 1
            if fonte != 'apresentacoes.json':
                outra = 'hub.csv' if fonte == 'tribunal.csv' else 'tribunal.csv'
                assert not any('ausente' in o['motivo'] and o['processo'] == CNJ
                               for o in relatorio['ocorrencias'] if o['fonte'] == outra)
        for resumo in relatorio['fontes'].values():
            assert resumo['recebidos'] == resumo['limpos'] + resumo['pendentes'] + resumo['descartados']

    def test_duplicata_identica_nao_oculta_divergencia_entre_fontes(self):
        arquivo = self.entrada / 'tribunal.csv'
        texto = arquivo.read_text(encoding='cp1252')
        arquivo.write_text(texto.rstrip('\n') + '\n' + texto.splitlines()[2] + '\n', encoding='cp1252')
        resultado, _ = self.rodar()
        with (self.saida / 'tribunal_limpo.csv').open(encoding='utf-8', newline='') as f:
            assert not any(r['numero_processo'].startswith('0023456') for r in csv.DictReader(f))
        assert any('Valor de face divergente' in o['motivo'] for o in resultado['ocorrencias'])
        assert resultado['fontes']['tribunal.csv']['descartados'] == 1
        for resumo in resultado['fontes'].values():
            assert resumo['recebidos'] == resumo['limpos'] + resumo['pendentes'] + resumo['descartados']

    def test_repeticao_com_mascara_diferente_e_consolidada(self):
        arquivo = self.entrada / 'apresentacoes.json'
        registros = json.loads(arquivo.read_bytes())
        registros.append({**registros[0], 'processo': valida_numero_processo(CNJ)})
        arquivo.write_text(json.dumps(registros))
        resultado, _ = self.rodar()
        limpos = json.loads((self.saida / 'apresentacoes_limpas.json').read_bytes())
        assert len([r for r in limpos if r['processo'] == CNJ]) == 1
        assert resultado['fontes']['apresentacoes.json']['descartados'] == 1

    @pytest.mark.parametrize('nova_data', ['2025-04-03', '2025-04-01'])
    def test_datas_divergentes_entre_fontes_ficam_pendentes(self, nova_data):
        arquivo = self.entrada / 'apresentacoes.json'
        dados = json.loads(arquivo.read_bytes())
        dados[0]['apresentado_em'] = nova_data
        arquivo.write_text(json.dumps(dados))
        resultado, _ = self.rodar()
        apresentacoes = json.loads((self.saida / 'apresentacoes_limpas.json').read_bytes())
        with (self.saida / 'hub_limpo.csv').open(encoding='utf-8', newline='') as f:
            hub = list(csv.DictReader(f))
        assert not any(r['processo'] == CNJ for r in apresentacoes + hub)
        pendencias = json.loads((self.saida / 'pendencias.json').read_bytes())
        for fonte in ('hub.csv', 'apresentacoes.json'):
            afetados = [p for p in pendencias if p['fonte'] == fonte and p['original']['processo'] == CNJ]
            assert len(afetados) == 1
            assert any('Data de apresentação divergente' in motivo for motivo in afetados[0]['motivos'])
        assert any('Data de apresentação divergente' in o['motivo'] for o in resultado['ocorrencias'])
        for resumo in resultado['fontes'].values():
            assert resumo['recebidos'] == resumo['limpos'] + resumo['pendentes'] + resumo['descartados']

    def test_datas_equivalentes_no_fuso_nao_geram_conflito(self):
        arquivo = self.entrada / 'apresentacoes.json'
        dados = json.loads(arquivo.read_bytes())
        dados[0]['apresentado_em'] = '2025-04-03T01:00:00Z'
        arquivo.write_text(json.dumps(dados))
        resultado, _ = self.rodar(fuso='America/Sao_Paulo')
        with (self.saida / 'hub_limpo.csv').open(encoding='utf-8', newline='') as f:
            assert any(r['processo'] == CNJ for r in csv.DictReader(f))
        assert not any('Data de apresentação divergente' in o['motivo'] and o['processo'] == CNJ
                       for o in resultado['ocorrencias'])

    def test_conflito_de_data_nao_duplica_pendencia_cadastral(self):
        arquivo = self.entrada / 'apresentacoes.json'
        dados = json.loads(arquivo.read_bytes())
        dados[1]['apresentado_em'] = '2025-04-02'
        arquivo.write_text(json.dumps(dados))
        resultado, _ = self.rodar()
        pendencias = json.loads((self.saida / 'pendencias.json').read_bytes())
        afetados = [p for p in pendencias if p['fonte'] == 'hub.csv'
                    and p['original']['processo'].startswith('0023456')]
        assert len(afetados) == 1
        assert any('Data de apresentação divergente' in motivo for motivo in afetados[0]['motivos'])
        for resumo in resultado['fontes'].values():
            assert resumo['recebidos'] == resumo['limpos'] + resumo['pendentes'] + resumo['descartados']
