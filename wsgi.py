from app import app

# WSGI entrypoint for hosting platforms (Render, PythonAnywhere, etc.)
# The hosting platform will import `app` from this module.

# Optionally, if you used factory pattern differently, you can use:
# from app import create_app; app = create_app()

if __name__ == '__main__':
    app.run()
