# NEML2 Language Support

VS Code extension providing language support for [NEML2](https://github.com/applied-material-modeling/neml2) input files.

## Features

- **Completion** — type names for `type = ` assignments, and option names with inline type hints inside typed blocks
- **Hover documentation** — docstrings for types and options shown on hover
- **Format on save** — re-indents the document consistently via the `nmhit` formatter

## Requirements

A Python environment with the [`neml2-langserv`](https://pypi.org/project/neml2-langserv/) package installed:

```
pip install neml2-langserv
```

That single install pulls in everything the language server needs — [`neml2`](https://pypi.org/project/neml2/) (≥ 2.1.4) for the type/option metadata, [`nmhit`](https://pypi.org/project/nmhit/) (≥ 0.1.2) for the formatter, and `pygls` for the LSP transport.

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

## MOOSE compatibility

This extension does **not** claim the `.i` extension globally, so existing MOOSE workflows are unaffected. Only files whose first line matches `# neml2` (optionally followed by other text) are switched to the `neml2` language mode.
