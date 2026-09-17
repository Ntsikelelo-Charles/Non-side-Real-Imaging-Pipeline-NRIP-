

from casacore.tables import table
from pathlib import Path
import argparse
import warnings
import yaml


# Fixed obs.yml filename
OBS_FILE = "xgc-reborn_obs.yml"


def get_field_names(ms):
    """Return field names grouped by OBS_MODE."""

    with table(ms + "/FIELD", readonly=True) as field:
        field_names = field.getcol("NAME")

    with table(ms + "/STATE", readonly=True) as state:
        state_obs_mode = state.getcol("OBS_MODE")

    with table(ms, readonly=True) as main:
        field_id = main.getcol("FIELD_ID")
        state_id = main.getcol("STATE_ID")

    field_modes = {}

    for fid, sid in zip(field_id, state_id):
        mode = str(state_obs_mode[sid]).upper()
        name = str(field_names[fid])

        if fid not in field_modes:
            field_modes[fid] = {
                "name": name,
                "modes": set(),
            }

        field_modes[fid]["modes"].add(mode)

    return field_modes


def find_fields(field_modes, search_string):
    """Find fields whose OBS_MODE contains search_string."""

    search_string = search_string.upper()

    names = []

    for info in field_modes.values():

        if any(search_string in mode for mode in info["modes"]):

            if info["name"] not in names:
                names.append(info["name"])

    return names


def get_single_field(field_modes, search_string):
    """Return one matching field, warning if multiple are found."""

    names = find_fields(field_modes, search_string)

    if not names:
        raise RuntimeError(
            f"No field found with OBS_MODE containing "
            f"'{search_string}'"
        )

    if len(names) > 1:

        warnings.warn(
            f"Multiple fields found for '{search_string}': "
            f"{names}. Using {names[0]}"
        )

    return names[0]


def get_frequency_range(ms):
    """
    Get the minimum and maximum channel frequency from
    the SPECTRAL_WINDOW table.

    Returns
    -------
    min_freq_hz : float
        Minimum frequency in Hz.

    max_freq_hz : float
        Maximum frequency in Hz.
    """

    spw_table = ms + "/SPECTRAL_WINDOW"

    with table(spw_table, readonly=True) as spw:
        chan_freq = spw.getcol("CHAN_FREQ")

    frequencies = chan_freq.flatten()

    min_freq_hz = frequencies.min()
    max_freq_hz = frequencies.max()

    return min_freq_hz, max_freq_hz


def determine_band(ms):
    """
    Determine the MeerKAT band from the frequency coverage.

    Currently supports the MeerKAT UHF and L bands.
    """

    min_freq_hz, max_freq_hz = get_frequency_range(ms)

    min_freq_mhz = min_freq_hz / 1e6
    max_freq_mhz = max_freq_hz / 1e6
    centre_freq_mhz = (min_freq_mhz + max_freq_mhz) / 2.0

    print()
    print("Observation frequency:")
    print(f"  Minimum: {min_freq_mhz:.2f} MHz")
    print(f"  Maximum: {max_freq_mhz:.2f} MHz")
    print(f"  Centre:  {centre_freq_mhz:.2f} MHz")

    # MeerKAT UHF
    if min_freq_mhz >= 500 and max_freq_mhz <= 1150:
        band = "UHF"

    # MeerKAT L band
    elif min_freq_mhz >= 800 and max_freq_mhz <= 1800:
        band = "L"

    else:
        raise RuntimeError(
            "Could not determine the MeerKAT band from the "
            f"frequency range "
            f"{min_freq_mhz:.2f}--{max_freq_mhz:.2f} MHz."
        )

    print(f"  Detected band: {band}")
    print()

    return band


def determine_crystallball_sky_model(bpcal, fcal):
    """
    Determine the Crystallball sky model from the
    bandpass and flux calibrator names.
    """

    bpcal = str(bpcal).strip().upper()
    fcal = str(fcal).strip().upper()

    calibrators = {bpcal, fcal}

    # PKS 1934-638
    if "J1939-6342" in calibrators:
        return (
            "data/crystallball/"
            "L-BAND/fitted.PKS1934.LBand.wsclean.cat.txt"
        )

    # PKS 0408-65
    if calibrators.intersection({
        "J0408-6545",
        "0408-65",
    }):
        return (
            "data/crystallball/"
            "L-BAND/fitted.PKS0407.LBand.wsclean.cat.txt"
        )

    raise RuntimeError(
        "Could not determine Crystallball sky model. "
        f"Bandpass calibrator: {bpcal}, "
        f"Flux calibrator: {fcal}. "
        "Expected J1939-6342, J0408-6545, or 0408-65."
    )


def update_obs_file(ms):
    """Update obs.yml using information from the MS."""

    obs_file = Path(OBS_FILE)

    if not Path(ms).exists():
        raise FileNotFoundError(
            f"Measurement Set does not exist: {ms}"
        )

    if not obs_file.exists():
        raise FileNotFoundError(
            f"{OBS_FILE} does not exist in the current directory."
        )

    print(f"Reading MS: {ms}")

    # ---------------------------------------------------------
    # Determine frequency band
    # ---------------------------------------------------------

    band = determine_band(ms)

    # ---------------------------------------------------------
    # Read YAML
    # ---------------------------------------------------------

    with open(obs_file, "r") as f:
        config = yaml.safe_load(f)

    obs = config["obs"]["default"]

    # ---------------------------------------------------------
    # Determine calibrator/target fields
    # ---------------------------------------------------------

    field_modes = get_field_names(ms)

    bpcal = get_single_field(
        field_modes,
        "CALIBRATE_BANDPASS"
    )

    fcal = get_single_field(
        field_modes,
        "CALIBRATE_FLUX"
    )

    gcal = get_single_field(
        field_modes,
        "CALIBRATE_PHASE"
    )

    target = get_single_field(
        field_modes,
        "TARGET"
    )

    # Polarization calibrator
    try:
        xcal = get_single_field(
            field_modes,
            "CALIBRATE_POLARIZATION"
        )

    except RuntimeError:
        xcal = get_single_field(
            field_modes,
            "UNKNOWN"
        )

    # ---------------------------------------------------------
    # Determine Crystallball sky model
    # ---------------------------------------------------------

    crystallball_sky_model = determine_crystallball_sky_model(
        bpcal,
        fcal
    )

    # ---------------------------------------------------------
    # Update ONLY the required fields
    # ---------------------------------------------------------

    obs["bpcal-field"] = bpcal
    obs["fcal-field"] = fcal
    obs["gcal-field"] = gcal
    obs["xcal-field"] = xcal
    obs["target-field"] = target

    # Update band
    obs["band"] = band

    # Update Crystallball sky model
    obs["crystallball_sky-model"] = crystallball_sky_model

    # ---------------------------------------------------------
    # Write YAML
    # ---------------------------------------------------------

    with open(obs_file, "w") as f:
        yaml.safe_dump(
            config,
            f,
            sort_keys=False,
            default_flow_style=False
        )

    # ---------------------------------------------------------
    # Report
    # ---------------------------------------------------------

    print("Updated field names:")
    print(f"  bpcal-field:  {bpcal}")
    print(f"  fcal-field:   {fcal}")
    print(f"  gcal-field:   {gcal}")
    print(f"  xcal-field:   {xcal}")
    print(f"  target-field: {target}")

    print()
    print(f"  band: {band}")

    print()
    print("Crystallball sky model:")
    print(f"  {crystallball_sky_model}")

    print()
    print(f"Updated: {OBS_FILE}")


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Update field names, band, and Crystallball "
            "sky model in obs.yml from a Measurement Set"
        )
    )

    parser.add_argument(
        "--ms",
        required=True,
        help="Path to the Measurement Set"
    )

    args = parser.parse_args()

    update_obs_file(args.ms)


if __name__ == "__main__":
    main()

