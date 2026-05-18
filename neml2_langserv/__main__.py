import argparse

from .server import server


def main() -> None:
    parser = argparse.ArgumentParser(description="NEML2 Language Server")
    parser.add_argument("--stdio", action="store_true", help="use stdio transport")
    parser.add_argument("--tcp", type=int, metavar="PORT", help="use TCP transport on PORT")
    args = parser.parse_args()

    if args.tcp:
        server.start_tcp("127.0.0.1", args.tcp)
    else:
        server.start_io()


if __name__ == "__main__":
    main()
