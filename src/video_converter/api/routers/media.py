from video_converter.api.routers._select import select_routes

router = select_routes(lambda path: path.startswith("/api/v1/media"))
