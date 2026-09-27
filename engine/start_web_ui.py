"""Start BrainEngine and open its Gradio control surface."""

import threading
import webbrowser

import uvicorn

import server


def main():
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:8001/ui/")).start()
    uvicorn.run(server.app, host="127.0.0.1", port=8001, use_colors=False)


if __name__ == "__main__":
    main()
