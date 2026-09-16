from noc_bridge.config import assert_production_config_is_safe
from noc_bridge.service import main

if __name__ == "__main__":
    assert_production_config_is_safe()
    main()
