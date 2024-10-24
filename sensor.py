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


from urllib.request import urlopen
import json
import pytz

import os.path

from datetime import datetime, timedelta
from urllib.error import URLError

import logging
import voluptuous as vol
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA

_LOGGER = logging.getLogger(__name__)

ATTR_STOP_ID = "stop_id"
ATTR_STOP_NAME = "stop_name"
ATTR_DUE_IN = "due_in"
ATTR_DELAY = "delay"
ATTR_REAL_TIME = "departure_time"
ATTR_DESTINATION = "direction"
ATTR_DIRECTION_ID = "direction_id"
ATTR_TRANS_TYPE = "type"
ATTR_TRANS_TYPE_RESTRICTION = "transit_type"
ATTR_TRIP_ID = "trip"
ATTR_LINE_NAME = "line_name"
ATTR_CONNECTION_STATE = "connection_status"

ATTR_STOP_ID_NEXT = "stop_id_next"
ATTR_STOP_NAME_NEXT = "stop_name_next"
ATTR_DUE_IN_NEXT = "due_in_next"
ATTR_DELAY_NEXT = "delay_next"
ATTR_REAL_TIME_NEXT = "departure_time_next"
ATTR_DESTINATION_NEXT = "direction_next"
ATTR_TRANS_TYPE_NEXT = "type_next"
ATTR_LINE_NAME_NEXT = "line_name_next"

CONF_NAME = "name"
CONF_STOP_ID = "stop_id"
CONF_DIRECTION_ID = "direction_id"
CONF_TRANS_TYPE_RESTRICTION = "transit_type"
CONF_MIN_DUE_IN = "walking_distance"
CONF_CACHE_PATH = "file_path"
CONF_CACHE_SIZE = "cache_size"

CONNECTION_STATE = "connection_state"
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
        self._isCacheValid = True
        self._timezone = self.hass_config.get("time_zone")
        self._name = name
        self._state = None
        self._stop_id = stop_id
        self._direction_id = direction_id
        self._transit_type = transit_type
        self.min_due_in = min_due_in
        self.url = "https://v6.bvg.transport.rest/stops/{}/departures?direction={}&duration={}".format(
            self._stop_id, self._direction_id, self._cache_size
        )
        #if transit mode specified, set only specified mode to true
        if self._transit_type is not None:
            for mode in TRANSIT_TYPES:
                if mode == self._transit_type.lower():
                    self.url += f"&{mode}=true"
                else:
                    self.url += f"&{mode}=false"
        self.data = None
        self.singleConnection = None
        self.nextSingleConnection = None
        self.file_path = os.path.join(self.hass_config.get("config_dir"), file_path)
        self.file_name = "bvg_{}.json".format(stop_id)
        self._con_state = {CONNECTION_STATE: CON_STATE_ONLINE}

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        
        if self.singleConnection is not None and self.nextSingleConnection is not None:
            return {
                ATTR_STOP_ID: self._stop_id,
                ATTR_STOP_NAME: self.singleConnection.get(ATTR_STOP_NAME),
                ATTR_DUE_IN: self.singleConnection.get(ATTR_DUE_IN),
                ATTR_DELAY: self.singleConnection.get(ATTR_DELAY),
                ATTR_REAL_TIME: self.singleConnection.get(ATTR_REAL_TIME),
                ATTR_DESTINATION: self.singleConnection.get(ATTR_DESTINATION),
                ATTR_TRANS_TYPE: self.singleConnection.get(ATTR_TRANS_TYPE),
                ATTR_LINE_NAME: self.singleConnection.get(ATTR_LINE_NAME),
                
                ATTR_STOP_NAME_NEXT: self.nextSingleConnection.get(ATTR_STOP_NAME),
                ATTR_DUE_IN_NEXT: self.nextSingleConnection.get(ATTR_DUE_IN),
                ATTR_DELAY_NEXT: self.nextSingleConnection.get(ATTR_DELAY),
                ATTR_REAL_TIME_NEXT: self.nextSingleConnection.get(ATTR_REAL_TIME),
                ATTR_DESTINATION_NEXT: self.nextSingleConnection.get(ATTR_DESTINATION),
                ATTR_TRANS_TYPE_NEXT: self.nextSingleConnection.get(ATTR_TRANS_TYPE),
                ATTR_LINE_NAME_NEXT: self.nextSingleConnection.get(ATTR_LINE_NAME),
            }
        elif self.singleConnection is not None:
            return {
                ATTR_STOP_ID: self._stop_id,
                ATTR_STOP_NAME: self.singleConnection.get(ATTR_STOP_NAME),
                ATTR_DUE_IN: self.singleConnection.get(ATTR_DUE_IN),
                ATTR_DELAY: self.singleConnection.get(ATTR_DELAY),
                ATTR_REAL_TIME: self.singleConnection.get(ATTR_REAL_TIME),
                ATTR_DESTINATION: self.singleConnection.get(ATTR_DESTINATION),
                ATTR_TRANS_TYPE: self.singleConnection.get(ATTR_TRANS_TYPE),
                ATTR_LINE_NAME: self.singleConnection.get(ATTR_LINE_NAME),

                ATTR_STOP_NAME_NEXT: "n/a",
                ATTR_DELAY_NEXT: "n/a",
                ATTR_REAL_TIME_NEXT: "n/a",
                ATTR_DESTINATION_NEXT: "n/a",
                ATTR_TRANS_TYPE_NEXT: "n/a",
                ATTR_LINE_NAME_NEXT: "n/a",
            }
        else:
            return {
                ATTR_STOP_ID: "n/a",
                ATTR_STOP_NAME: "n/a",
                ATTR_DELAY: "n/a",
                ATTR_REAL_TIME: "n/a",
                ATTR_DESTINATION: "n/a",
                ATTR_TRANS_TYPE: "n/a",
                ATTR_LINE_NAME: "n/a",

                ATTR_STOP_NAME_NEXT: "n/a",
                ATTR_DELAY_NEXT: "n/a",
                ATTR_REAL_TIME_NEXT: "n/a",
                ATTR_DESTINATION_NEXT: "n/a",
                ATTR_TRANS_TYPE_NEXT: "n/a",
                ATTR_LINE_NAME_NEXT: "n/a",
            }

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "min"

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        if self.singleConnection is not None:
            return ICONS.get(self.singleConnection.get(ATTR_TRANS_TYPE))
        else:
            return ICONS.get(None)

    def update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        self.fetchDataFromURL()
        self.singleConnection = self.getSingleConnection(
            self.min_due_in, 0
        )
        self.nextSingleConnection = self.getSingleConnection(
            self.min_due_in, 1
        )
        if self.singleConnection is not None and len(self.singleConnection) > 0:
            self._state = self.singleConnection.get(ATTR_DUE_IN)
        else:
            self._state = "n/a"

    # only custom code beyond this line
    # @property
    def fetchDataFromURL(self):
        try:
            _LOGGER.warning(f"Attempting to open URL: {self.url}")
            with urlopen(self.url, timeout=5) as response:
                source = response.read().decode("utf8")
                self.data = json.loads(source)
                if self._con_state.get(CONNECTION_STATE) is CON_STATE_OFFLINE:
                    _LOGGER.warning("Connection to BVG API re-established")
                    self._con_state.update({CONNECTION_STATE: CON_STATE_ONLINE})
                # write the response to a file for caching if connection is not available, which seems to happen from time to time
                try:
                    _LOGGER.warning("in try about to open file")
                    with open("{}{}".format(self.file_path, self.file_name), "w") as fd:
                        try:
                            json.dump(self.data, fd, ensure_ascii=False)
                        except Exception as e:
                            _LOGGER.error(f"Error writing data to cache file: {e}")

                        self._cache_creation_date = datetime.now(
                            pytz.timezone(self._timezone)
                        )
                except IOError as e:
                    _LOGGER.error(
                        "Could not write file. Please check your configuration and read/write access for path:{}".format(
                            self.file_path
                        )
                    )
                    _LOGGER.error("I/O error({}): {}".format(e.errno, e.strerror))
        except HTTPError as e:
            _LOGGER.error(f"HTTPError in fetchDataFromURL: {e.code} - {e.reason}")
        except URLError as e:
            _LOGGER.error(f"Error in fetchDataFromURL: {e}")
            if self._con_state.get(CONNECTION_STATE) is CON_STATE_ONLINE:
                _LOGGER.debug(e)
                _LOGGER.warning("Connection to BVG API lost, using local cache instead")
                self._con_state.update({CONNECTION_STATE: CON_STATE_OFFLINE})
            self.fetchDataFromFile()
        except Exception as e:
            _LOGGER.error(f"Unexpected error in fetchDataFromURL: {e}")

    def fetchDataFromFile(self):
        try:
            with open("{}{}".format(self.file_path, self.file_name), "r") as fd:
                self.data = json.load(fd)
        except IOError as e:
            _LOGGER.error(
                "Could not read file. Please check your configuration and read/write access for path: {}".format(
                    self.file_path
                )
            )
            _LOGGER.error("I/O error({}): {}".format(e.errno, e.strerror))

    def getSingleConnection(self, min_due_in, nmbr):
        timetable_l = list()
        date_now = datetime.now(pytz.timezone(self.hass_config.get("time_zone")))
        for pos in self.data["departures"]:
            if pos.get("when") is None:
                # skip this step if no departure time
                continue
            dep_time = datetime.strptime(pos["when"][:-6], "%Y-%m-%dT%H:%M:%S")
            dep_time = pytz.timezone("Europe/Berlin").localize(dep_time)
            delay = (pos["delay"] // 60) if pos["delay"] is not None else 0
            departure_td = dep_time - date_now
            # check if connection is not in the past
            if departure_td > timedelta(days=0):
                departure_td = departure_td.seconds // 60
                if departure_td >= min_due_in:
                    timetable_l.append(
                        {
                            ATTR_DESTINATION: pos["direction"],
                            ATTR_REAL_TIME: dep_time,
                            ATTR_DUE_IN: departure_td,
                            ATTR_DELAY: delay,
                            ATTR_TRIP_ID: pos["tripId"],
                            ATTR_STOP_NAME: pos["stop"]["name"],
                            ATTR_TRANS_TYPE: pos["line"]["product"],
                            ATTR_LINE_NAME: pos["line"]["name"],
                        }
                    )
                    _LOGGER.debug("Connection found")
                else:
                    _LOGGER.debug(
                        "Connection is due in under {} minutes".format(
                            min_due_in
                        )
                    )
            else:
                _LOGGER.debug("Connection lies in the past")
        try:
            _LOGGER.debug("Valid connection found")
            _LOGGER.debug("Connection: {}".format(timetable_l))
            return timetable_l[int(nmbr)]
        except IndexError as e:
            if self.isCacheValid():
                _LOGGER.warning(
                    "No valid connection found for sensor named {}. Please check your configuration.".format(
                        self.name
                    )
                )
                self._isCacheValid = True
            else:
                if self._isCacheValid:
                    _LOGGER.warning("Cache is outdated.")
                self._isCacheValid = False
                # _LOGGER.error(e)
            return None

    def isCacheValid(self):
        date_now = datetime.now(pytz.timezone(self.hass_config.get("time_zone")))
        # If the component is starting without internet connection
        if self._cache_creation_date is None:
            self._cache_creation_date = datetime.fromtimestamp(
                os.path.getmtime("{}{}".format(self.file_path, self.file_name)),
                pytz.timezone(self._timezone),
            )
        td = self._cache_creation_date - date_now
        td = td.seconds
        _LOGGER.debug("td is: {}".format(td))
        if td > (self._cache_size * 60):
            _LOGGER.debug("Cache Age (not valid): {}".format(td // 60))
            return False
        else:
            return True
