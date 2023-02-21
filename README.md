# BVG Sensor Component for Home Assistant

The BVG Sensor can be used to display real-time public transport data for the city of Berlin within the BVG (Berliner Verkehrsbetriebe) route network. 
The sensor will display the minutes until the next departure for the configured station and direction. The provided data is in real-time and does include actual delays. If you want to customize the sensor you can use the provided sensor attributes. You can also define a walking distance from your home/work, so only departures that are reachable will be shown. 

During testing I found that the API frequently becomes unavailable, possibly to keep the amount of requests low. Therefore this component keeps a local copy of the data (90 minutes). The local data is only beeing used while "offline" and is beeing refreshed when the API endpoint becomes available again. 

This component uses the API endpoint that provides data from the BVG HAFAS API by [Jannis Redmann](https://github.com/derhuerst/).
Without his fantastic work, this component would not possible!

# Installation

Clone the repository into your ``/config/custom_components/`` folder and rename it from ``bvg-sensor`` to ``bvg``. If it does not already exist, create the missing ``custom_components`` folder.

# Prerequisites

You will need to specify at least a ``stop_id`` and a ``direction_id`` for the connection you would like to display. Both values are the ``id`` number of a station.

To find a station's ``id`` use the following link: https://v5.bvg.transport.rest/stops/nearby?latitude=52.52725&longitude=13.4123 and replace the values for ```latitude=``` and ```longitude=``` with your coordinates. You can get those e.g. from Google Maps.
Find your station's `id` within the json repsonse in your browser.

You can also search for a station by name by using the following link: https://v5.bvg.transport.rest/locations?query=alexanderplatz and replace the ```query=``` value with the name of the station you are looking for. Note that partial matches are supported for searching.

### Example:
You want to display the departure times from "S+U Schönhauser Allee" in direction of "Alexanderplatz"

#### get the stop_id:

Link: https://v5.bvg.transport.rest/locations?query=schonhauser

You may get multiple results depending on your search, so scroll until you find the correct data:

``
{
"type": "stop","id": "900000110001","name": "S+U Schönhauser Allee","location": {"type": "location","id": "900110001","latitude": 52.549339,"longitude": 13.415142},"products": {"suburban": true,"subway": true,"tram": true,"bus": true,"ferry": false,"express": false,"regional": false},"stationDHID": "de:11000:900110001"}
``

Your ``stop_id`` for ``"S+U Schönhauser Allee"`` would be ``"900000110001"``

#### get the direction:

Specify the direction of travel by retriving the ``id`` of the final station, or of any station along the route you wish to take. By selecting a station close to your ``stop_id``, you can help accomodate for construction that might cause the line's final destination to temporarily change. In this example, even though I want to travel to ``Alexanderplatz``, I am using ``U Eberswalder`` as the direction. As fo this writing, this will find U2 trains that stop at U Senefelder Platz, where a transfer is required due to consutrction on the U2 line.

https://v5.bvg.transport.rest/locations?query=eberswalder

``
{"type": "stop","id": "900000110006","name": "U Eberswalder Str.","location": {"type": "location","id": "900110006","latitude": 52.541024,"longitude": 13.412157},"products": {"suburban": false,"subway": true,"tram": true,"bus": true,"ferry": false,"express": false,"regional": false},"stationDHID": "de:11000:900110006"}
``

Your ``direction_id`` for ``"U Eberswalder Str."`` would be ``"900000110006"``

#### specify transit type (optional):

For some routes, multiple transit methods may start at your ``stop_id`` and pass through your ``direction``. You can restrict the results to a specific type of transit by including the optional ``transit_type`` parameter. The default is all transit types. Valid options are: `suburban`, `subway`, `tram`, `bus`, `ferry`, `regional`, or `express`

# Configuration

To add the BVG Sensor Component to Home Assistant, add the following to your configuration.yaml file:

```yaml
# Example configuration.yaml entry
- platform: bvg
    stop_id: your stop id
    direction: the final destination for your connection
````

- **stop_id** *(Required)*: The id for your station.
- **direction_id** *(Required)*: The id for a station along your route, or the destination.
- **name** *(optional)*: Name your sensor, especially if you create multiple instance of the sensor give them different names. * (Default=BVG)*
- **transit_type** *(optional)*: The type of transit you would like to be restricted to, i.e. `tram`. By default, all modes of transit are shown.
- **walking_distance** *(optional)*: specify the walking distance in minutes from your home/location to the station. Only connections that are reachable in a timley manner will be shown. Set it to ``0`` if you want to disable this feature. *(Default=10)*
- **file_path** *(optional)*: path where you want your station specific data to be saved. *(Default= your home assistant config directory e.g. "conf/" )*

### Example Configuration:
```yaml
sensor:
  - platform: bvg
    name: U2 to Alexanderplatz
    stop_id: "900000110001" # U Schonhauser
    direction_id: "900000110006" # U Eberswalder
    transit_type: "subway" # don't include trams or busses
    walking_distance: 5
    file_path: "/tmp/"
```
