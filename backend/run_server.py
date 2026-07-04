import uvicorn
from app_runtime import configure_logging, configure_model_cache, prepend_bundled_bin_to_path
from port_utils import assert_port_available
from settings import get_host, get_port

prepend_bundled_bin_to_path()
configure_model_cache()
configure_logging()

host = get_host()
port = get_port()
assert_port_available(host, port, "hermes-voice-desktop")
uvicorn.run("server:app", host=host, port=port, log_level="info")
