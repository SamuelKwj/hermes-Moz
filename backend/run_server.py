import uvicorn
from app_runtime import configure_logging, configure_model_cache, prepend_bundled_bin_to_path
from settings import get_host, get_port

prepend_bundled_bin_to_path()
configure_model_cache()
configure_logging()

uvicorn.run("server:app", host=get_host(), port=get_port(), log_level="info")
