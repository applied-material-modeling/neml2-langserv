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

import { InspectResult, showInspectionPanel } from "./inspectPanel";

const NEML2_MARKER = /^#\s*neml2\b/;
const PACKAGE_NAME = "neml2-langserv";
const MODULE_NAME = "neml2_langserv";

let client: LanguageClient | undefined;

/** Capability state pushed by the server via the `neml2/capabilities` notification. */
const inspectCapabilities = {
  jsonSupported: false,
  neml2Version: null as string | null,
  reason: null as string | null,
};

interface ListedModel {
  name: string;
  line: number;
}

async function syncLanguage(doc: TextDocument): Promise<void> {
  if (doc.lineCount === 0) return;
  const isNeml2Marker = NEML2_MARKER.test(doc.lineAt(0).text);

  if (isNeml2Marker && doc.languageId !== "neml2" && doc.fileName.endsWith(".i")) {
    await languages.setTextDocumentLanguage(doc, "neml2");
  } else if (!isNeml2Marker && doc.languageId === "neml2" && doc.fileName.endsWith(".i")) {
    await languages.setTextDocumentLanguage(doc, "moose");
  }
}

export async function activate(context: ExtensionContext): Promise<void> {
  extensionContext = context;

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
    initializationOptions: {
      codeLensEnabled: workspace
        .getConfiguration("neml2")
        .get<boolean>("inspect.codeLens.enabled", true),
      load: workspace
        .getConfiguration("neml2")
        .get<string[]>("load", []),
    },
  };

  client = new LanguageClient(
    "neml2",
    "NEML2 Language Server",
    serverOptions,
    clientOptions
  );

  // Receive the capability probe result so the palette command can decide
  // whether to invoke the server or short-circuit with a helpful warning.
  client.onNotification("neml2/capabilities", (params: {
    inspectJsonSupported?: boolean;
    neml2InspectVersion?: string | null;
    inspectReason?: string | null;
  }) => {
    inspectCapabilities.jsonSupported = !!params?.inspectJsonSupported;
    inspectCapabilities.neml2Version = params?.neml2InspectVersion ?? null;
    inspectCapabilities.reason = params?.inspectReason ?? null;
    commands.executeCommand(
      "setContext",
      "neml2.inspectAvailable",
      inspectCapabilities.jsonSupported,
    );
  });

  // Forward config changes so the server can refresh code lenses / reload
  // user-extension paths on the fly.
  context.subscriptions.push(
    workspace.onDidChangeConfiguration((e) => {
      const codeLensChanged = e.affectsConfiguration(
        "neml2.inspect.codeLens.enabled",
      );
      const loadChanged = e.affectsConfiguration("neml2.load");
      if (!codeLensChanged && !loadChanged) return;
      const payload: { codeLensEnabled?: boolean; load?: string[] } = {};
      if (codeLensChanged) {
        payload.codeLensEnabled = workspace
          .getConfiguration("neml2")
          .get<boolean>("inspect.codeLens.enabled", true);
      }
      if (loadChanged) {
        payload.load = workspace
          .getConfiguration("neml2")
          .get<string[]>("load", []);
      }
      client?.sendNotification("neml2/configChanged", payload);
    }),
  );

  // Inspect command — invoked by CodeLens (with args pre-filled) or palette.
  context.subscriptions.push(
    commands.registerCommand(
      "neml2.inspectModel",
      async (uriArg?: string, modelArg?: string) => {
        await runInspectCommand(uriArg, modelArg);
      },
    ),
  );

  await client.start();
  context.subscriptions.push({ dispose: () => client?.stop() });
}

async function runInspectCommand(
  uriArg?: string,
  modelArg?: string,
): Promise<void> {
  if (!client) {
    window.showErrorMessage("NEML2: language server is not running.");
    return;
  }

  if (!inspectCapabilities.jsonSupported) {
    const version = inspectCapabilities.neml2Version ?? "unknown";
    const reason =
      inspectCapabilities.reason ??
      "neml2-inspect in the active neml2 build does not support --json output.";
    window.showWarningMessage(
      `NEML2 Inspect requires neml2 ≥ 3.0.2 (--json output mode). ` +
        `Detected: ${version}. ${reason}`,
    );
    return;
  }

  const targetUri =
    uriArg ?? window.activeTextEditor?.document.uri.toString();
  if (!targetUri) {
    window.showErrorMessage("NEML2: no active NEML2 document to inspect.");
    return;
  }

  let modelName = modelArg;
  if (!modelName) {
    let listed: ListedModel[] = [];
    try {
      listed = await client.sendRequest<ListedModel[]>("neml2/listModels", {
        uri: targetUri,
      });
    } catch (err) {
      window.showErrorMessage(`NEML2: failed to list models — ${String(err)}`);
      return;
    }
    if (!listed || listed.length === 0) {
      window.showInformationMessage(
        "NEML2: no models found under [Models] in this document.",
      );
      return;
    }
    const picked = await window.showQuickPick(
      listed.map((m) => ({
        label: m.name,
        description: `line ${m.line + 1}`,
      })),
      { placeHolder: "Select a model to inspect" },
    );
    if (!picked) return;
    modelName = picked.label;
  }

  let result: InspectResult;
  try {
    result = await client.sendRequest<InspectResult>("neml2/inspect", {
      uri: targetUri,
      model: modelName,
    });
  } catch (err) {
    window.showErrorMessage(
      `NEML2: inspect request failed — ${String(err)}`,
    );
    return;
  }

  // Show the panel regardless of success/failure; the panel renders the error
  // verbatim when present, preserving multi-line C++ diagnostics.
  // Use the same context the command was registered against.
  const ctx = extensionContext;
  if (!ctx) {
    window.showErrorMessage("NEML2: extension context unavailable.");
    return;
  }
  showInspectionPanel(ctx, modelName, result);

  if (result.error) {
    // Brief toast so the user notices something went wrong even if they don't
    // see the side panel.
    window.showWarningMessage(`NEML2: inspect of "${modelName}" failed.`);
  }
}

let extensionContext: ExtensionContext | undefined;

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
