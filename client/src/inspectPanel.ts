/**
 * Singleton webview that renders the result of a `neml2-inspect --json` invocation.
 *
 * A single panel is reused across clicks (re-titled and re-populated) to avoid
 * tab clutter. HTML is built with strict CSP and a small inline stylesheet bound
 * to VS Code's theme variables, so it tracks light/dark/high-contrast themes
 * without any JavaScript or external resources.
 */
import {
  ExtensionContext,
  ViewColumn,
  WebviewPanel,
  window,
} from "vscode";

/** Item rendered in the Inputs / Outputs / Parameters / Buffers tables. */
export interface InspectItem {
  name: string;
  type: string;
  /** Only populated for Parameters and Buffers. */
  dtype?: string;
  /** Only populated for Parameters and Buffers. */
  device?: string;
}

/** Mirror of the JSON payload returned by the server's `neml2/inspect` request. */
export interface InspectResult {
  retcode: number;
  name?: string;
  host?: string;
  inputs?: InspectItem[];
  outputs?: InspectItem[];
  parameters?: InspectItem[];
  buffers?: InspectItem[];
  /** Non-null iff the invocation failed. The text is rendered verbatim. */
  error?: string | null;
}

let panel: WebviewPanel | undefined;

/** Open or refocus the inspect panel and populate it with the given result. */
export function showInspectionPanel(
  context: ExtensionContext,
  modelName: string,
  data: InspectResult,
): void {
  if (!panel) {
    panel = window.createWebviewPanel(
      "neml2.inspectModel",
      `Inspect: ${modelName}`,
      { viewColumn: ViewColumn.Beside, preserveFocus: true },
      { enableScripts: false, retainContextWhenHidden: true },
    );
    panel.onDidDispose(() => {
      panel = undefined;
    }, null, context.subscriptions);
  } else {
    panel.title = `Inspect: ${modelName}`;
    panel.reveal(panel.viewColumn ?? ViewColumn.Beside, true);
  }
  panel.webview.html = renderHtml(panel.webview.cspSource, modelName, data);
}

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderTags(item: InspectItem): string {
  const tags: string[] = [];
  if (item.type) tags.push(item.type);
  if (item.dtype) tags.push(item.dtype);
  if (item.device) tags.push(item.device);
  return tags.map((t) => `<code>[${escapeHtml(t)}]</code>`).join("");
}

function renderSection(title: string, items: InspectItem[] | undefined): string {
  if (!items || items.length === 0) return "";
  const rows = items
    .map(
      (item) =>
        `<tr><td class="name">${escapeHtml(item.name)}</td>` +
        `<td class="tags">${renderTags(item)}</td></tr>`,
    )
    .join("");
  return `<section><h2>${escapeHtml(title)}</h2><table><tbody>${rows}</tbody></table></section>`;
}

function renderHtml(cspSource: string, modelName: string, data: InspectResult): string {
  const style = `
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground);
           background: var(--vscode-editor-background); padding: 1rem;
           font-size: var(--vscode-font-size); }
    h1 { font-size: 1.3em; margin: 0 0 0.2em; font-weight: 600; }
    h2 { font-size: 1.05em; margin: 1.4em 0 0.4em; font-weight: 600;
         border-bottom: 1px solid var(--vscode-textBlockQuote-border); padding-bottom: 0.2em; }
    .meta { color: var(--vscode-descriptionForeground); margin-bottom: 0.8em;
            font-size: 0.92em; }
    table { border-collapse: collapse; width: 100%; }
    td { padding: 3px 8px; vertical-align: top; }
    td.name { font-family: var(--vscode-editor-font-family); white-space: nowrap;
              color: var(--vscode-symbolIcon-variableForeground, var(--vscode-foreground)); }
    td.tags code { background: var(--vscode-textCodeBlock-background);
                   color: var(--vscode-textPreformat-foreground, var(--vscode-foreground));
                   padding: 1px 6px; margin-right: 4px; border-radius: 3px;
                   font-family: var(--vscode-editor-font-family); font-size: 0.9em; }
    pre.error { color: var(--vscode-errorForeground); white-space: pre-wrap;
                background: var(--vscode-textCodeBlock-background);
                padding: 0.75em; border-radius: 4px; border-left: 3px solid var(--vscode-errorForeground); }
    .empty { color: var(--vscode-descriptionForeground); font-style: italic; }
  `;

  const csp =
    `default-src 'none'; style-src ${cspSource} 'unsafe-inline'; ` +
    `img-src ${cspSource}; font-src ${cspSource};`;

  let body: string;
  if (data.error) {
    body =
      `<h1>${escapeHtml(modelName)}</h1>` +
      `<div class="meta">neml2-inspect reported an error (retcode ${data.retcode ?? "?"}).</div>` +
      `<pre class="error">${escapeHtml(data.error)}</pre>`;
  } else {
    const name = data.name || modelName;
    const showHost = data.host && data.host !== name;
    const meta = showHost
      ? `<div class="meta">Host: <code>${escapeHtml(data.host!)}</code></div>`
      : "";
    const sections =
      renderSection("Inputs", data.inputs) +
      renderSection("Outputs", data.outputs) +
      renderSection("Parameters", data.parameters) +
      renderSection("Buffers", data.buffers);
    const fallback =
      sections === ""
        ? `<div class="empty">No inputs, outputs, parameters, or buffers reported.</div>`
        : "";
    body = `<h1>${escapeHtml(name)}</h1>${meta}${sections}${fallback}`;
  }

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <title>${escapeHtml(modelName)}</title>
  <style>${style}</style>
</head>
<body>
${body}
</body>
</html>`;
}
