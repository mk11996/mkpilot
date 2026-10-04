#!/usr/bin/env python3
"""
Initialize GD (Mazda openpilot fork) specific parameters
"""

from common.params import Params

def init_gd_params():
    """Initialize GD-specific parameters with default values"""
    params = Params()

    # Initialize gd_0813 parameter if not set
    # Default to False (use hybrid GD-0816 model)
    if params.get("gd_0813") is None:
        params.put_bool("gd_0813", False)
        print("Initialized gd_0813 parameter: False (using GD-0816 hybrid model)")
    else:
        current_value = params.get_bool("gd_0813")
        model_name = "GD-0813 (legacy)" if current_value else "GD-0816 (hybrid)"
        print(f"gd_0813 parameter already set: {current_value} (using {model_name} model)")

if __name__ == "__main__":
    init_gd_params()
