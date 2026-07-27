from video_converter.api.routers._select import select_routes

router = select_routes(lambda path: path.startswith("/health") or path == "/api/v1/worker/health")
