# Lazy — don't eagerly import routes here.  The router pulls in
# triggers → agent → quo_tools → open_phone.service, creating a
# circular import.  Consumers should import the router directly:
#   from api.src.open_phone.routes import router
