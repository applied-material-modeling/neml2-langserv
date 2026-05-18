import {
  commands,
  ExtensionContext,
  extensions,
  languages,
  TextDocument,
  window,
  workspace,
} from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from "vscode-languageclient/node";

const NEML2_MARKER = /^#\s*neml2\b/;
const PACKAGE_NAME = "neml2-langserv";
const MODULE_NAME = "neml2_langserv";

let client: LanguageClient | undefined;

async function syncLanguage(doc: TextDocument): Promise<void> {
  if (doc.lineCount === 0) return;
  const isNeml2Marker = NEML2_MARKER.test(doc.lineAt(0).text);

  if (isNeml2Marker && doc.languageId !== "neml2") {
    await languages.setTextDocumentLanguage(doc, "neml2");
  } else if (!isNeml2Marker && doc.languageId === "neml2" && doc.fileName.endsWith(".i")) {
    await languages.setTextDocumentLanguage(doc, "moose");
  }
}

export async function activate(context: ExtensionContext): Promise<void> {
  // Check language on open and on every edit that touches line 0.
  context.subscriptions.push(
    workspace.onDidOpenTextDocument(syncLanguage),
    workspace.onDidChangeTextDocument((e) => {
      const touchesLine0 = e.contentChanges.some(
        (c) => c.range.start.line === 0 || c.range.end.line === 0
      );
      if (touchesLine0) syncLanguage(e.document);
    })
  );
  for (const doc of workspace.textDocuments) {
    await syncLanguage(doc);
  }

  const python = await findPython();
  if (!python) {
    window.showErrorMessage(
      "NEML2: Could not find a Python interpreter. " +
        'Set "python.defaultInterpreterPath" in your VS Code settings.'
    );
    return;
  }

  if (!(await ensureServerInstalled(python))) {
    return;
  }

  const serverOptions: ServerOptions = {
    command: python,
    args: ["-m", MODULE_NAME, "--stdio"],
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ language: "neml2" }],
    synchronize: {},
  };

  client = new LanguageClient(
    "neml2",
    "NEML2 Language Server",
    serverOptions,
    clientOptions
  );

  await client.start();
  context.subscriptions.push({ dispose: () => client?.stop() });
}

export function deactivate(): Thenable<void> | undefined {
  return client?.stop();
}

async function findPython(): Promise<string | undefined> {
  // Prefer the interpreter selected in the Python extension.
  try {
    const pyExt = extensions.getExtension("ms-python.python");
    if (pyExt) {
      if (!pyExt.isActive) await pyExt.activate();
      const api = pyExt.exports as {
        settings?: { getExecutionDetails?: () => { execCommand?: string[] } };
      };
      const cmd = api?.settings?.getExecutionDetails?.()?.execCommand;
      if (cmd && cmd.length > 0) return cmd[0];
    }
  } catch {
    // fall through
  }

  // Fall back to PATH
  const candidates = ["python3", "python"];
  const { execFile } = await import("child_process");
  const { promisify } = await import("util");
  const exec = promisify(execFile);
  for (const cand of candidates) {
    try {
      await exec(cand, ["--version"]);
      return cand;
    } catch {
      // try next
    }
  }
  return undefined;
}

/**
 * Verify that the language server package is importable from `python`. If
 * not, prompt the user to install it (or pick a different interpreter).
 *
 * Returns true if the package is available and the caller may proceed to
 * start the language client. Returns false if the user dismissed the prompt
 * or chose to switch interpreters — in that case activation should bail.
 */
async function ensureServerInstalled(python: string): Promise<boolean> {
  const { execFile } = await import("child_process");
  const { promisify } = await import("util");
  const exec = promisify(execFile);

  try {
    await exec(python, ["-c", `import ${MODULE_NAME}`]);
    return true;
  } catch {
    // Not installed — fall through to prompt.
  }

  const installLabel = "Install";
  const chooseLabel = "Choose interpreter";
  const pick = await window.showErrorMessage(
    `NEML2: '${PACKAGE_NAME}' is not installed in ${python}. ` +
      "It provides the language server (hover, completion, formatting).",
    installLabel,
    chooseLabel
  );

  if (pick === installLabel) {
    const term = window.createTerminal({ name: "NEML2: Install language server" });
    term.show(true);
    // Quote the interpreter path in case it contains spaces.
    term.sendText(`"${python}" -m pip install ${PACKAGE_NAME}`);
    window.showInformationMessage(
      "NEML2: Run 'Developer: Reload Window' once the install finishes to activate the language server."
    );
  } else if (pick === chooseLabel) {
    const pyExt = extensions.getExtension("ms-python.python");
    if (pyExt) {
      await commands.executeCommand("python.setInterpreter");
      window.showInformationMessage(
        "NEML2: After selecting a new interpreter, reload the window."
      );
    } else {
      window.showWarningMessage(
        "NEML2: The Python extension (ms-python.python) is not installed, so I can't open its interpreter picker."
      );
    }
  }

  return false;
}
