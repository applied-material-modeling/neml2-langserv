# NEML2 Language Support

VS Code extension providing language support for [NEML2](https://github.com/applied-material-modeling/neml2) input files.

## Features

- **Completion** — type names for `type = ` assignments, and option names with inline type hints inside typed blocks
- **Hover documentation** — docstrings for types and options shown on hover
- **Format on save** — re-indents the document consistently via the `nmhit` formatter
- **Inspect model** — a 🔬 CodeLens above every `[model]` block under `[Models]` (and a matching `NEML2: Inspect Model` palette command) runs `neml2-inspect` on the current buffer and renders the model's inputs, outputs, parameters, and buffers in a side-panel webview. Requires `neml2 ≥ 3.0.2`; on older builds the lens is silently hidden and the rest of the extension keeps working.
- **User extensions** — set `neml2.load` to a list of `.py` files, package directories, or dotted module names; each entry is forwarded as `--load` to `neml2-syntax` and `neml2-inspect` so custom `@register_native` classes appear alongside the built-in ones in completions, hovers, and Inspect.

## Requirements

A Python environment with the [`neml2-langserv`](https://pypi.org/project/neml2-langserv/) package installed:

```
pip install neml2-langserv
```

That single install pulls in everything the language server needs — [`neml2`](https://pypi.org/project/neml2/) (≥ 3.0.2) for the type/option metadata and the inspect feature, [`nmhit`](https://pypi.org/project/nmhit/) (≥ 0.2.2) for the formatter, and `pygls` for the LSP transport.

The extension runs the server with whichever Python interpreter is selected in the [Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python). If `neml2-langserv` is not installed in that interpreter on first activation, the extension prompts you to install it.

## Setup

NEML2 input files share the `.i` extension with MOOSE input files. To tell the extension that a file is a NEML2 input, add the following as the **first line**:

```
# neml2
```

Files without this marker are treated as MOOSE input (or plain text) and the NEML2 language server will not activate for them.

## Format on save

Enable the built-in VS Code setting to auto-format on save:

```jsonc
// .vscode/settings.json
{
  "[neml2]": {
    "editor.formatOnSave": true
  }
}
```

## Inspect a model

With `neml2 ≥ 3.0.2` installed, a `🔬 Inspect model` CodeLens appears above each `[name]` sub-block under `[Models]`. Click it to open a side-panel webview listing the model's inputs, outputs, parameters, and buffers (with their tensor types, dtypes, and device tags) for the *current buffer* — unsaved edits are picked up automatically.

The same action is available from the command palette as `NEML2: Inspect Model`, which prompts you to pick a model.

To hide the lenses (the palette command stays available), set:

```jsonc
// settings.json
{
  "neml2.inspect.codeLens.enabled": false
}
```

## Loading user extensions

If your input files reference `@register_native` types defined outside the `neml2` package (e.g. project-specific models), tell the language server to import them at startup so completions, hovers, and Inspect resolve those types. Add an entry per extension to `neml2.load` — each is either a filesystem path to a `.py` file or a package directory, or a dotted module name on the active interpreter's `sys.path`:

```jsonc
// .vscode/settings.json
{
  "neml2.load": [
    "${workspaceFolder}/extensions/my_models.py",
    "my_project.neml2_ext"
  ]
}
```

The list is forwarded one-to-one as `--load` arguments to both `neml2-syntax` (driving completions/hovers) and `neml2-inspect` (driving the Inspect Model panel). Entries import in the order given, so a later module may depend on names registered by an earlier one. Changing the setting reloads the in-process syntax catalog automatically.

## MOOSE compatibility

This extension does **not** claim the `.i` extension globally, so existing MOOSE workflows are unaffected. Only files whose first line matches `# neml2` (optionally followed by other text) are switched to the `neml2` language mode.
