from django.http import HttpResponse

import zoo


class ZooCors:
    """CORS for the panel (ZOO_PANEL_ORIGIN) on the /_zoo/ routes it reads."""

    PREFIXES = ("/_zoo/health", "/_zoo/probe", "/_zoo/trace/", "/_zoo/chain/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(self.PREFIXES):
            return self.get_response(request)
        origin = request.headers.get("Origin")
        if request.method == "OPTIONS":
            resp = HttpResponse(status=204)
            for k, v in zoo.preflight_headers(origin).items():
                resp[k] = v
            return resp
        resp = self.get_response(request)
        for k, v in zoo.cors_headers(origin).items():
            resp[k] = v
        return resp
