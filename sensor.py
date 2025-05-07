# Version History:
# Version 0.1 - initial release
# Version 0.2 - added multiple destinations, optimized error logging
# Version 0.3 fixed encoding, simplified config for direction
# Version 0.3.1 fixed a bug when departure is null
# Version 0.3.2 bufix for TypeError
# Version 0.3.3 switched to timezone aware objects, cache_size added to config parameters, optimized logging
# Version 0.3.4 fixed encoding (issue #3), fixed typo in filepath
# Version 0.4.0 renamed device_state_attributes to extra_state_attributes, added version to manifest, updated API url to v5
# Version 0.4.1 replaced direction with direction ID, added transit type restriction
# Version 0.4.2 added retrieval of next train/bus and corresponding _next attributes
# Version 0.4.3 updated BVG api to v6
# Version 0.5.0 Refactored to store multiple departures in a list, removed _NEXT attributes, fetches up to 4 departures by default.

from urllib.request import urlopen
from urllib.error import URLError, HTTPError # Added HTTPError for explicit import
import json
import pytz

import os.path

from datetime import datetime, timedelta

import logging
import voluptuous as vol
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA

_LOGGER = logging.getLogger(__name__)

# --- Attribute Constants ---
# Top-level attributes, primarily for the *first* upcoming departure
ATTR_STOP_ID = "stop_id"
ATTR_STOP_NAME = "stop_name"
ATTR_DUE_IN = "due_in"
ATTR_DELAY = "delay"
ATTR_REAL_TIME = "departure_time"
ATTR_DESTINATION = "direction"
ATTR_TRANS_TYPE = "type"
ATTR_TRIP_ID = "trip" # This key is used in the departure objects, not usually a top-level attribute
ATTR_LINE_NAME = "line_name"
ATTR_CONNECTION_STATE = "connection_status"

# Attribute to hold a list of all fetched departure details
ATTR_DEPARTURES = "departures"

CONF_NAME = "name"
CONF_STOP_ID = "stop_id"
CONF_DIRECTION_ID = "direction_id"
CONF_TRANS_TYPE_RESTRICTION = "transit_type"
CONF_MIN_DUE_IN = "walking_distance"
CONF_CACHE_PATH = "file_path"
CONF_CACHE_SIZE = "cache_size"

CONNECTION_STATE = "connection_state" # Internal key for self._con_state
CON_STATE_ONLINE = "online"
CON_STATE_OFFLINE = "offline"

ICONS = {
    "suburban": "mdi:subway-variant",
    "subway": "mdi:subway",
    "tram": "mdi:tram",
    "bus": "mdi:bus",
    "regional": "mdi:train",
    "ferry": "mdi:ferry",
    "express": "mdi:train",
    "n/a": "mdi:clock",
    None: "mdi:clock",
}

TRANSIT_TYPES = [
    "suburban",
    "subway",
    "tram",
    "bus",
    "ferry",
    "regional",
    "express"
]

SCAN_INTERVAL = timedelta(seconds=60)
NUM_DEPARTURES_TO_FETCH = 4 # Configure how many departures to fetch

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_STOP_ID): cv.string,
        vol.Required(CONF_DIRECTION_ID): cv.string,
        vol.Optional(CONF_TRANS_TYPE_RESTRICTION): cv.string,
        vol.Optional(CONF_MIN_DUE_IN, default=10): cv.positive_int,
        vol.Optional(CONF_CACHE_PATH, default="/"): cv.string,
        vol.Optional(CONF_NAME, default="BVG"): cv.string,
        vol.Optional(CONF_CACHE_SIZE, default=90): cv.positive_int,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Setup the sensor platform."""
    stop_id = config[CONF_STOP_ID]
    direction_id = config.get(CONF_DIRECTION_ID)
    transit_type = config.get(CONF_TRANS_TYPE_RESTRICTION)
    min_due_in = config.get(CONF_MIN_DUE_IN)
    file_path = config.get(CONF_CACHE_PATH)
    name = config.get(CONF_NAME)
    cache_size = config.get(CONF_CACHE_SIZE)
    add_entities(
        [BvgSensor(name, stop_id, direction_id, transit_type, min_due_in, file_path, hass, cache_size)]
    )


class BvgSensor(Entity):
    """Representation of a Sensor."""

    def __init__(
        self, name, stop_id, direction_id, transit_type, min_due_in, file_path, hass, cache_size
    ):
        """Initialize the sensor."""
        self.hass_config = hass.config.as_dict()
        self._cache_size = cache_size
        self._cache_creation_date = None
        self._isCacheValid = True # Note: This variable's usage in getSingleConnection seems to be for logging based on cache age.
        self._timezone = self.hass_config.get("time_zone")
        self._name = name
        self._state = "n/a" # Default state
        self._stop_id = stop_id
        self._direction_id = direction_id
        self._transit_type = transit_type
        self.min_due_in = min_due_in
        # API URL construction for fetching departures
        self.url = "https://v6.bvg.transport.rest/stops/{}/departures?direction={}&duration={}".format(
            self._stop_id, self._direction_id, self._cache_size
        )
        # Apply transit type restriction if specified
        if self._transit_type is not None:
            for mode in TRANSIT_TYPES:
                if mode == self._transit_type.lower():
                    self.url += f"&{mode}=true"
                else:
                    self.url += f"&{mode}=false"
        
        self.data = None  # Holds the raw JSON response from the API or cache
        self.departures = [] # List to store processed departure data (dictionaries)
        
        self.file_path = os.path.join(self.hass_config.get("config_dir", ""), file_path) # Ensure config_dir is present
        self.file_name = "bvg_{}.json".format(stop_id)
        self._con_state = {CONNECTION_STATE: CON_STATE_ONLINE} # Internal connection state tracking

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor (due_in for the first departure)."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attrs = {ATTR_CONNECTION_STATE: self._con_state.get(CONNECTION_STATE, CON_STATE_OFFLINE)}

        # Populate top-level attributes from the first departure, if available
        if self.departures: # Check if the departures list is not empty
            first_departure = self.departures[0]
            attrs[ATTR_STOP_ID] = self._stop_id # Configured stop ID
            attrs[ATTR_STOP_NAME] = first_departure.get(ATTR_STOP_NAME)
            attrs[ATTR_DUE_IN] = first_departure.get(ATTR_DUE_IN)
            attrs[ATTR_DELAY] = first_departure.get(ATTR_DELAY)
            attrs[ATTR_REAL_TIME] = first_departure.get(ATTR_REAL_TIME)
            attrs[ATTR_DESTINATION] = first_departure.get(ATTR_DESTINATION)
            attrs[ATTR_TRANS_TYPE] = first_departure.get(ATTR_TRANS_TYPE)
            attrs[ATTR_LINE_NAME] = first_departure.get(ATTR_LINE_NAME)
            # ATTR_TRIP_ID is available within each departure dict in ATTR_DEPARTURES
        else:
            # Default values if no departures found
            attrs[ATTR_STOP_ID] = self._stop_id
            attrs[ATTR_STOP_NAME] = "n/a"
            attrs[ATTR_DUE_IN] = "n/a"
            attrs[ATTR_DELAY] = "n/a"
            attrs[ATTR_REAL_TIME] = "n/a"
            attrs[ATTR_DESTINATION] = "n/a"
            attrs[ATTR_TRANS_TYPE] = "n/a"
            attrs[ATTR_LINE_NAME] = "n/a"

        # Add the list of all departures. Each item is a dictionary.
        # The keys within these dictionaries are ATTR_DESTINATION, ATTR_REAL_TIME, etc.
        processed_departures = []
        for dep in self.departures:
            processed_departures.append({
                ATTR_DESTINATION: dep.get(ATTR_DESTINATION),
                ATTR_REAL_TIME: dep.get(ATTR_REAL_TIME),
                ATTR_DUE_IN: dep.get(ATTR_DUE_IN),
                ATTR_DELAY: dep.get(ATTR_DELAY),
                ATTR_TRIP_ID: dep.get(ATTR_TRIP_ID),
                ATTR_STOP_NAME: dep.get(ATTR_STOP_NAME),
                ATTR_TRANS_TYPE: dep.get(ATTR_TRANS_TYPE),
                ATTR_LINE_NAME: dep.get(ATTR_LINE_NAME),
            })
        attrs[ATTR_DEPARTURES] = processed_departures
        
        return attrs

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "min"

    @property
    def icon(self):
        """Icon to use in the frontend, based on the first departure's type."""
        if self.departures and self.departures[0] is not None:
            return ICONS.get(self.departures[0].get(ATTR_TRANS_TYPE))
        else:
            return ICONS.get(None) # Default icon

    def update(self):
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        """
        self.fetchDataFromURL() # Populates self.data with API response or cached data
        
        self.departures = [] # Clear previous departures
        
        # Check if data was successfully fetched and contains departures
        if self.data and self.data.get("departures"):
            # getSingleConnection processes self.data to find the n-th valid departure
            for i in range(NUM_DEPARTURES_TO_FETCH):
                connection = self.getSingleConnection(self.min_due_in, i)
                if connection:
                    self.departures.append(connection)
                else:
                    # No valid departure found for this slot (i). Add a placeholder.
                    _LOGGER.debug(f"No valid connection found for index {i} for sensor {self.name}. Adding placeholder.")
                    placeholder_departure = {
                        ATTR_DESTINATION: "n/a",
                        ATTR_REAL_TIME: "n/a",
                        ATTR_DUE_IN: "n/a", 
                        ATTR_DELAY: 0, # Default to 0 for consistency
                        ATTR_TRIP_ID: "n/a_placeholder", # Special marker for placeholder
                        ATTR_STOP_NAME: self.data.get("departures")[0].get("stop", {}).get("name") if self.data.get("departures") else self._stop_id, # Try to get actual stop name, fallback
                        ATTR_TRANS_TYPE: "n/a", # Will result in default clock icon
                        ATTR_LINE_NAME: "n/a",
                    }
                    self.departures.append(placeholder_departure)
                    # Continue to the next iteration of the loop
        else:
            _LOGGER.debug(f"No departure data available in self.data to process for sensor {self.name}. Filling all slots with placeholders.")
            # If no data at all, fill all NUM_DEPARTURES_TO_FETCH slots with placeholders
            for _ in range(NUM_DEPARTURES_TO_FETCH):
                placeholder_departure = {
                    ATTR_DESTINATION: "n/a",
                    ATTR_REAL_TIME: "n/a",
                    ATTR_DUE_IN: "n/a",
                    ATTR_DELAY: 0,
                    ATTR_TRIP_ID: "n/a_placeholder",
                    ATTR_STOP_NAME: self._stop_id, # Fallback to configured stop_id
                    ATTR_TRANS_TYPE: "n/a",
                    ATTR_LINE_NAME: "n/a",
                }
                self.departures.append(placeholder_departure)
            
        # Update the primary sensor state based on the first departure
        # (which could now be a placeholder)
        if self.departures: # Should always be true now due to placeholder logic
            first_departure = self.departures[0]
            # If ATTR_DUE_IN is "n/a" (string from placeholder), state becomes "n/a"
            # If it's an int (from a real departure), state is that int.
            self._state = first_departure.get(ATTR_DUE_IN) 
        else:
            # This case should ideally not be reached if placeholders are always added
            self._state = "n/a" 

    def fetchDataFromURL(self):
        """Fetches data from the BVG API URL and handles caching."""
        try:
            _LOGGER.debug(f"Attempting to open URL: {self.url}") # Changed to debug
            with urlopen(self.url, timeout=5) as response:
                source = response.read().decode("utf8") # Assuming utf8, as per original
                self.data = json.loads(source)
                if self._con_state.get(CONNECTION_STATE) == CON_STATE_OFFLINE: # Check current state before logging
                    _LOGGER.warning("Connection to BVG API re-established")
                self._con_state[CONNECTION_STATE] = CON_STATE_ONLINE # Update state
                
                # Write response to cache file
                try:
                    # _LOGGER.debug("Attempting to write to cache file: {}{}".format(self.file_path, self.file_name)) # Changed to debug
                    # Ensure directory exists (optional, good practice if file_path can be deep)
                    # os.makedirs(self.file_path, exist_ok=True) # If file_path is just a directory
                    with open(os.path.join(self.file_path, self.file_name), "w", encoding="utf-8") as fd: # Added encoding
                        json.dump(self.data, fd, ensure_ascii=False)
                        self._cache_creation_date = datetime.now(
                            pytz.timezone(self._timezone)
                        )
                except IOError as e:
                    _LOGGER.error(
                        f"Could not write cache file to {os.path.join(self.file_path, self.file_name)}. Check configuration and permissions."
                    )
                    _LOGGER.error(f"I/O error({e.errno}): {e.strerror}")
                except Exception as e: # Catch other potential errors during file write
                    _LOGGER.error(f"Error writing data to cache file: {e}")

        except HTTPError as e: # Specific catch for HTTPError
            _LOGGER.error(f"HTTPError fetching data: {e.code} - {e.reason}. URL: {self.url}")
            if self._con_state.get(CONNECTION_STATE) == CON_STATE_ONLINE:
                _LOGGER.warning("Connection to BVG API lost (HTTPError), attempting to use local cache.")
            self._con_state[CONNECTION_STATE] = CON_STATE_OFFLINE
            self.fetchDataFromFile() # Attempt to load from cache
        except URLError as e: # Catch other URL related errors (timeout, DNS etc.)
            _LOGGER.error(f"URLError fetching data: {e.reason}. URL: {self.url}")
            if self._con_state.get(CONNECTION_STATE) == CON_STATE_ONLINE:
                _LOGGER.warning("Connection to BVG API lost (URLError), attempting to use local cache.")
            self._con_state[CONNECTION_STATE] = CON_STATE_OFFLINE
            self.fetchDataFromFile() # Attempt to load from cache
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Error decoding JSON response from BVG API: {e}")
            self._con_state[CONNECTION_STATE] = CON_STATE_OFFLINE # Treat as offline if response is malformed
            self.fetchDataFromFile() # Attempt to load from cache
        except Exception as e: # Generic catch-all for unexpected errors
            _LOGGER.error(f"Unexpected error in fetchDataFromURL: {e}")
            # Potentially set to offline and try cache, depending on desired robustness
            # self._con_state[CONNECTION_STATE] = CON_STATE_OFFLINE
            # self.fetchDataFromFile()


    def fetchDataFromFile(self):
        """Fetches data from the local cache file."""
        try:
            cache_file_full_path = os.path.join(self.file_path, self.file_name)
            # _LOGGER.debug(f"Attempting to read from cache file: {cache_file_full_path}") # Changed to debug
            if not os.path.exists(cache_file_full_path):
                _LOGGER.warning(f"Cache file not found: {cache_file_full_path}. No data loaded from cache.")
                self.data = None # Ensure data is None if cache file doesn't exist
                return

            with open(cache_file_full_path, "r", encoding="utf-8") as fd: # Added encoding
                self.data = json.load(fd)
                # Update cache creation date from file modification time if not set by successful API call
                if self._cache_creation_date is None:
                    self._cache_creation_date = datetime.fromtimestamp(
                        os.path.getmtime(cache_file_full_path),
                        pytz.timezone(self._timezone)
                    )
        except IOError as e:
            _LOGGER.error(
                f"Could not read cache file from {os.path.join(self.file_path, self.file_name)}. Check configuration."
            )
            _LOGGER.error(f"I/O error({e.errno}): {e.strerror}")
            self.data = None # Ensure data is None on error
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Error decoding JSON from cache file {os.path.join(self.file_path, self.file_name)}: {e}")
            self.data = None # Ensure data is None on error
        except Exception as e:
            _LOGGER.error(f"Unexpected error in fetchDataFromFile: {e}")
            self.data = None


    def getSingleConnection(self, min_due_in, nmbr):
        """
        Processes self.data (fetched from API/cache) and returns the 'nmbr'-th valid departure.
        A departure is valid if it's not in the past and meets the min_due_in criteria.
        """
        if not self.data or "departures" not in self.data:
            _LOGGER.debug(f"getSingleConnection: No self.data or no 'departures' key in self.data for sensor {self.name}.")
            return None

        timetable_l = list()
        # Ensure timezone is correctly obtained for 'now'
        try:
            current_timezone = pytz.timezone(self._timezone if self._timezone else "UTC") # Fallback to UTC if not set
        except pytz.exceptions.UnknownTimeZoneError:
            _LOGGER.error(f"Unknown timezone configured: {self._timezone}. Defaulting to UTC.")
            current_timezone = pytz.timezone("UTC")
        
        date_now = datetime.now(current_timezone)

        for pos in self.data["departures"]:
            if pos.get("when") is None: # Departure time is missing
                _LOGGER.debug(f"Skipping entry due to missing 'when' field: {pos.get('tripId', 'Unknown Trip')}")
                continue
            
            try:
                # Parse ISO 8601 datetime string (e.g., "2023-05-01T10:00:00+02:00")
                # The API provides timezone-aware strings.
                dep_time_naive_str = pos["when"]
                # Try to parse with timezone, then without if it fails (older formats might exist)
                try:
                    dep_time = datetime.fromisoformat(dep_time_naive_str)
                except ValueError: # Handle cases like "2024-05-07T10:00:00" (naive)
                    dep_time_naive = datetime.strptime(dep_time_naive_str.split('+')[0].split('Z')[0], "%Y-%m-%dT%H:%M:%S")
                    # Assume Berlin timezone for naive times from BVG API, then convert to user's configured timezone
                    dep_time_berlin = pytz.timezone("Europe/Berlin").localize(dep_time_naive)
                    dep_time = dep_time_berlin.astimezone(current_timezone)

                # Ensure dep_time is timezone-aware for comparison with date_now
                if dep_time.tzinfo is None or dep_time.tzinfo.utcoffset(dep_time) is None:
                    _LOGGER.warning(f"Departure time for trip {pos.get('tripId')} is naive. Assuming Europe/Berlin.")
                    dep_time = pytz.timezone("Europe/Berlin").localize(dep_time).astimezone(current_timezone)
                else:
                    # Convert to the system's timezone if different for consistent comparison
                    dep_time = dep_time.astimezone(current_timezone)

            except ValueError as e:
                _LOGGER.error(f"Could not parse departure time string: {pos['when']}. Error: {e}. Skipping entry.")
                continue

            delay = (pos.get("delay", 0) // 60) if pos.get("delay") is not None else 0 # Delay in minutes
            
            # Calculate difference in minutes
            if dep_time > date_now:
                departure_td_total_seconds = (dep_time - date_now).total_seconds()
                departure_minutes = departure_td_total_seconds // 60
                
                if departure_minutes >= min_due_in:
                    timetable_l.append(
                        {
                            ATTR_DESTINATION: pos.get("direction"),
                            ATTR_REAL_TIME: dep_time.isoformat(), # Store as ISO string
                            ATTR_DUE_IN: int(departure_minutes),
                            ATTR_DELAY: delay,
                            ATTR_TRIP_ID: pos.get("tripId"),
                            ATTR_STOP_NAME: pos.get("stop", {}).get("name"),
                            ATTR_TRANS_TYPE: pos.get("line", {}).get("product"),
                            ATTR_LINE_NAME: pos.get("line", {}).get("name"),
                        }
                    )
                    # _LOGGER.debug("Valid connection added to timetable_l.")
                else:
                    _LOGGER.debug(
                        f"Connection {pos.get('line', {}).get('name')} to {pos.get('direction')} is due in {departure_minutes} min (less than {min_due_in} min walking distance). Skipping."
                    )
            else:
                _LOGGER.debug(f"Connection {pos.get('line', {}).get('name')} to {pos.get('direction')} is in the past. Skipping.")
        
        # Sort timetable by due_in to ensure correct order if API doesn't guarantee it
        timetable_l.sort(key=lambda x: x[ATTR_DUE_IN])

        try:
            # _LOGGER.debug(f"Timetable for getSingleConnection(nmbr={nmbr}): {timetable_l}")
            return timetable_l[int(nmbr)]
        except IndexError:
            # This is expected if fewer than 'nmbr' departures are found.
            # The logging for "No valid connection found" might be too noisy if it happens often.
            # The self._isCacheValid logic here seems to be for determining if a warning is needed.
            if self.isCacheValid(): # isCacheValid checks if the cache file is recent
                _LOGGER.debug(
                    f"No valid connection found for index {nmbr} for sensor {self.name}. This is normal if fewer than {NUM_DEPARTURES_TO_FETCH} departures are available."
                )
                # self._isCacheValid = True # This line seems redundant as isCacheValid() doesn't change state.
            else:
                # Only log warning if cache is also outdated.
                if self._isCacheValid: # This condition will be false if isCacheValid() returned false.
                    _LOGGER.warning(f"Cache is outdated for sensor {self.name}, and no connection found at index {nmbr}.")
                # self._isCacheValid = False # This variable's state management might need review if it's critical.
            return None
        except Exception as e:
            _LOGGER.error(f"Unexpected error in getSingleConnection when accessing timetable_l: {e}")
            return None


    def isCacheValid(self):
        """Checks if the cache file is considered recent based on CONF_CACHE_SIZE."""
        # If _cache_creation_date is not set (e.g., first run, no API success yet),
        # try to get it from file modification time.
        if self._cache_creation_date is None:
            try:
                cache_file_full_path = os.path.join(self.file_path, self.file_name)
                if os.path.exists(cache_file_full_path):
                    self._cache_creation_date = datetime.fromtimestamp(
                        os.path.getmtime(cache_file_full_path),
                        pytz.timezone(self._timezone if self._timezone else "UTC") # Fallback for timezone
                    )
                else:
                    _LOGGER.debug("isCacheValid: Cache file does not exist, cannot determine creation date from file.")
                    return False # No cache, so it's not "valid" in terms of being recent data.
            except pytz.exceptions.UnknownTimeZoneError:
                _LOGGER.error(f"Unknown timezone for cache validation: {self._timezone}. Cannot validate cache age accurately.")
                return False # Cannot validate if timezone is broken
            except Exception as e:
                _LOGGER.error(f"Error accessing cache file modification time: {e}")
                return False # Assume not valid if we can't check.

        if self._cache_creation_date is None: # Still None after trying file mod time
            _LOGGER.debug("isCacheValid: _cache_creation_date is None, cache is not considered valid.")
            return False

        # Ensure timezone for 'now'
        try:
            current_timezone = pytz.timezone(self._timezone if self._timezone else "UTC")
        except pytz.exceptions.UnknownTimeZoneError:
            _LOGGER.error(f"Unknown timezone for cache validation (now): {self._timezone}. Defaulting to UTC.")
            current_timezone = pytz.timezone("UTC")

        date_now = datetime.now(current_timezone)
        
        # Calculate age of cache
        cache_age_delta = date_now - self._cache_creation_date
        cache_age_seconds = cache_age_delta.total_seconds()
        
        # Cache is valid if its age in seconds is less than or equal to cache_size in minutes * 60
        # And age must be positive (cache_creation_date should not be in the future)
        if cache_age_seconds >= 0 and cache_age_seconds <= (self._cache_size * 60):
            _LOGGER.debug(f"Cache is valid. Age: {cache_age_seconds // 60} minutes.")
            return True
        else:
            if cache_age_seconds < 0:
                _LOGGER.warning(f"Cache creation date ({self._cache_creation_date}) is in the future compared to now ({date_now}). Assuming cache is not valid.")
            else:
                _LOGGER.debug(f"Cache is outdated. Age: {cache_age_seconds // 60} minutes (Max allowed: {self._cache_size} minutes).")
            return False

