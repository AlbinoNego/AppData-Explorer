# AData Explorer

Programa para Windows que analisa o `AppData` do usuario atual e mostra quanto cada pasta ocupa.

Ele verifica:

- `%APPDATA%` (`AppData\Roaming`)
- `%LOCALAPPDATA%` (`AppData\Local`)
- `AppData\LocalLow`

O programa apenas le arquivos e gera relatorio. Ele nao apaga arquivos.

## Recursos

- Lista pastas por tamanho, do maior para o menor.
- Mostra origem provavel do app, jogo ou cache.
- Classifica a confianca como `Alta`, `Media`, `Baixa` ou `Nenhuma`.
- Explica o motivo da identificacao.
- Exporta relatorio em CSV ou JSON.
- Ordena colunas ao clicar no cabecalho, incluindo colunas numericas.
- Tem barra de navegacao com abas de relatorio e explorar.
- Permite navegar dentro das pastas analisadas pelo proprio programa.
- Mostra tamanho de arquivos, pastas e subpastas no modo `Explorar`.
- Permite abrir arquivos/pastas diretamente ou revelar no Explorer do Windows.
- Permite excluir arquivos e pastas dentro do AppData com confirmacao.
- Ignora links simbolicos para evitar loops.
- Continua a analise mesmo quando encontra arquivos bloqueados ou sem permissao.

## Uso do Explorar

Depois de analisar o AppData, selecione uma pasta no relatorio e abra a aba `Explorar`.
Tambem e possivel dar duplo clique em uma linha do relatorio para abrir aquela pasta no `Explorar`.

No `Explorar`:

- duplo clique em uma pasta navega para dentro dela;
- duplo clique em um arquivo tenta abrir o arquivo no app padrao do Windows;
- `Mostrar no Explorer` abre o item no Explorer do Windows;
- `Excluir selecionado` remove o item permanentemente, depois de confirmar.

A exclusao e limitada aos caminhos de AppData detectados pelo programa.

## Como executar pelo Python

No PowerShell:

```powershell
cd C:\Users\cryst\Documents\ADataExplorer
python .\src\appdata_explorer.py
```

## Como gerar EXE

Instale o PyInstaller:

```powershell
python -m pip install pyinstaller
```

Depois rode:

```powershell
cd C:\Users\cryst\Documents\ADataExplorer\tools
.\build_exe.ps1
```

O executavel sera criado em:

```text
C:\Users\cryst\Documents\ADataExplorer\dist\ADataExplorer.exe
```

## Observacao sobre "dono" da pasta

O Windows nem sempre registra oficialmente qual programa criou cada pasta em `AppData`.
Por isso o AData Explorer usa inferencia:

- nomes de pastas;
- programas instalados no Registro do Windows;
- padroes conhecidos de apps, launchers e jogos;
- extensoes e nomes de arquivos encontrados;
- local especial, como `LocalLow`, comum em jogos.

Quando nao ha evidencia suficiente, o programa marca como `Desconhecido`.
