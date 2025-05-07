# BVG Sensor Component for Home Assistant

The BVG Sensor can be used to display real-time public transport data for the city of Berlin within the BVG (Berliner Verkehrsbetriebe) route network. 
The sensor will display the minutes until the next departure for the configured station and direction. The provided data is in real-time and does include actual delays. If you want to customize the sensor you can use the provided sensor attributes. You can also define a walking distance from your home/work, so only departures that are reachable will be shown. 

During testing I found that the API frequently becomes unavailable, possibly to keep the amount of requests low. Therefore this component keeps a local copy of the data (90 minutes). The local data is only beeing used while "offline" and is beeing refreshed when the API endpoint becomes available again. 

This component uses the API endpoint that provides data from the BVG HAFAS API by [Jannis Redmann](https://github.com/derhuerst/).
Without his fantastic work, this component would not possible!

This fork from [disrupted/bvg-sensor](https://github.com/disrupted/bvg-sensor) has been updated to use the latest BVG API (currently [v6](https://v6.bvg.transport.rest/)), and retrieves details for multiple upcoming departures.

# Installation

Clone the repository into your ``/config/custom_components/`` folder and rename it from ``bvg-sensor`` to ``bvg``. If it does not already exist, create the missing ``custom_components`` folder.

**0.5.0 Breaking chage:** The component was updated to use a departures object to store multiple future departures, instead of using `_next` to access only one additional departure. See _Fetching future departures_ at the end of this Read Me for an example.

# Prerequisites

You will need to specify at least a ``stop_id`` and a ``direction_id`` for the connection you would like to display. Both values are the ``id`` number of a station, can be found by using the BVG API to search by geolocation or station name. The JSON object returned by the BVG API is easily viewed in a browser window, using the `locations` method.

To find a station's ``id`` use the following link: https://v6.bvg.transport.rest/locations?query=alexanderplatz and replace the ```query=``` value with the name of the station you are looking for. Note that partial matches are supported for searching.

You can also search for a station by geo-location by using the following link: https://v6.bvg.transport.rest/locations/nearby?latitude=52.52725&longitude=13.4123 and replace the values for ```latitude=``` and ```longitude=``` with your coordinates. You can get those e.g. from Google Maps.

Full documentation for the BVG's API can be found here: https://v6.bvg.transport.rest/

### Example:
You want to display the departure times from "S+U Schönhauser Allee" in direction of "Alexanderplatz"

#### get the stop_id:

Link: https://v6.bvg.transport.rest/locations?query=schonhauser&results=1

You can restrict the number of results returned by using the ``&results=1`` URL parameter:

``
{
		"type": "stop",
		"id": "900110001",
		"name": "S+U Schönhauser Allee (Berlin)",
		"location": {
			"type": "location",
			"id": "900110001",
			"latitude": 52.549339,
			"longitude": 13.415142
		},
		"products": {
			"suburban": true,
			"subway": true,
			"tram": true,
			"bus": true,
			"ferry": false,
			"express": false,
			"regional": false
		}
	}
``

Your ``stop_id`` for ``"S+U Schönhauser Allee"`` would be ``"900110001"``

#### get the direction:

Specify the direction of travel by retriving the ``id`` of the final station, or of any station along the route you wish to take. By selecting a direction station close to your ``stop_id``, you can help accomodate for construction that might cause the line's final destination to temporarily change. In this example, even though I want to travel to ``Alexanderplatz``, I am using ``U Eberswalder`` as the direction. During construction that required a bus transfer at U Senefelderplatz, this would still find U2 trains in the direction of Alexanderplatz, even though Senefelderplatz was the temporary last stop.

https://v6.bvg.transport.rest/locations?query=eberswalder&results=1

``
{"type": "stop","id": "900110006","name": "U Eberswalder Str. (Berlin)","location": {...}}
``

Your ``direction_id`` for ``"U Eberswalder Str."`` would be ``"900110006"``

#### specify transit type (optional):

For some routes, multiple transit methods may travel from your ``stop_id`` to your ``direction_id``. You can restrict the results to a specific type of transit by including the optional ``transit_type`` parameter. The default is all transit types. Valid options are: `suburban`, `subway`, `tram`, `bus`, `ferry`, `regional`, or `express`

# Configuration

To add the BVG Sensor Component to Home Assistant, add the following to your configuration.yaml file:

```yaml
# Example configuration.yaml entry structure
sensor:
  - platform: bvg
    name: U2 Rosa-Luxemburg-Platz
    stop_id: your stop id
    direction_id: the id indicating your direction of travel
    walking_distance: 5
    file_path: "/tmp/"
```

- **stop_id** *(Required)*: The id for your station.
- **direction_id** *(Required)*: The id for a station along your route, or the destination.
- **name** *(optional)*: Name your sensor, especially if you create multiple instances of the sensor give them different names. * (Default=BVG)*
- **transit_type** *(optional)*: The type of transit you would like to be restricted to, i.e. `tram`. By default, all modes of transit are shown.
- **walking_distance** *(optional)*: specify the walking distance in minutes from your home/location to the station. Only connections that are reachable in a timley manner will be shown. Set it to ``0`` if you want to disable this feature. *(Default=10)*
- **file_path** *(optional)*: path where you want your station specific data to be saved. *(Default= your home assistant config directory e.g. "conf/" )*

### Sample Configuration:
```yaml
sensor:
  - platform: bvg
    name: U2 to Alexanderplatz
    stop_id: "900110001" # U Schonhauser ID
    direction_id: "900110006" # U Eberswalder ID
    transit_type: "subway" # don't include trams or busses
    walking_distance: 5 # skip departures less than 5 minutes from now
    file_path: "/tmp/"
```

# Available sensor states

Some useful states available from the sensor:

- **stop_name**: BVG station name
- **due_in**: minutes until departure
- **delay**: delay from normally scheduled time (will be negative if early)
- **departure_time**: full date/timestamp for departure time
- **direction**: final destination
- **type**: transit type
- **line_name**: BVG route name

### Invalid/unavailable departures

If the data for a departure is unable to be retrieved, the returned data will be populated with `n/a`

# Displaying future departures

By default, 4 upcoming departures are fetched. To display a given departure in the future, you can access the sensor's `departures` object and then specify the desired state. So if you want to fetch the `due_in` time for the 2nd upcoming departure:

```yaml
{{ state_attr('sensor.u2_to_alexanderplatz', 'departures')[1].due_in }}
```

This method replaces the previously used `_next` that was appended to a state name. Accessing departure `0` in this way is equivalent to default states retrieved by the sensor.
