# Design

Base: `PREVIA_POST_DOWN.html` (protótipo de interface recebido). Preservados: composição, paleta (grafite `#101211`,
verde-lima `#cdfa72`), tipografia, painéis arredondados, campo de link destacado, botões grandes, barra lateral
no desktop e coluna única no celular (≤700 px, sem menu lateral).

O CSS original está intacto no início de `static/styles.css`; os acréscimos estão no fim do arquivo, marcados.

Mudanças da prévia para a versão funcional:
- Removidos: tarja "PRÉVIA DO LAYOUT", botão "Explorar o layout" (virou "Como funciona") e os resultados ilustrativos.
- Painel de acesso (senha) no mesmo estilo do painel de link.
- "Seu espaço" no topo virou o botão "Sair" (mesmo formato de pílula).
- Cartões de mídia mostram a miniatura real, os dados medidos no arquivo e os estados reais
  (Preparar → Preparando arquivo… → Baixar). Cartão do ZIP mantém o desenho do "batch-card".
- Legenda com "Copiar legenda" e link "Abrir publicação original".
- Logo: arquivo original sem alteração, com o mesmo filtro CSS da prévia.
