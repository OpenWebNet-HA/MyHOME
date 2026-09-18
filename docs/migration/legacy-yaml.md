# Legacy Configuration History (< v0.9 & v0.9.x)

> [!WARNING]
> **You are viewing the v2 (Beta) Documentation.**
> In v2.0+, setup and device onboarding are **100% UI-first** via Config Flow and Auto-Discovery. Manual YAML files are **not used** for device creation.
> * To upgrade an existing v0.9.4 installation, see the [**Upgrade from 0.9.4 Guide**](upgrade-from-094.md).
> * For the complete, dedicated manual for `/config/myhome.yaml`, visit the [**v0.9.4 Legacy Documentation**](../../0.9.4/).

This page is preserved for historical reference and documentation of the deprecated pre-v0.9 and v0.9.x YAML schemas.

Once your gateway is integrated in Home Assistant, you can start adding your different devices.  
This configuration previously took place in your `configuration.yaml` file (pre-v0.9) or `/config/myhome.yaml` (v0.9.x).

### Lights
For clarity, the OpenWebNet elements of WHO 1 have been split in two domains, first and obvious one is lights:

```yaml
light:
  - platform: myhome
    devices:
      garage:
        where: '01'
        name: Garage
        dimmable: False
        manufacturer: Arnould
        model: 64391
      dining_room:
        where: '17'
        name: Dining room
        dimmable: False
        manufacturer: BTicino
        model: F411U2
      main_bedroom_1:
        where: '23'
        name: Main bedroom
        dimmable: True
        manufacturer: BTicino
        model: F418
```
Here, as almost everywhere throughout this configuration, `where` is the OpenWebNet address of your device (the 'APL').  
`name` is an optional "friendly name".  
`dimmable` is an optional boolean defaulting to `False` that you need to set to `True` if your device supports dimming. (Only "F418" and "F418U2" DIN modules seem to do to this day)  
`manufacturer` and `model` are optional and purely cosmetic (as they are reported in the device detail in Home-Assistant's interface).

### Switches
The second part of WHO 1 is switches, it is a domain that you will use if you have controller not attached to lights, but rather to power outlets for instance.

```yaml
switch:
  - platform: myhome
    devices:
      bed_heater:
        where: '0211'
        name: Mattress heating pad
        class: outlet
        manufacturer: BTicino
        model: F411U2
      door_bell:
        where: '0515'
        name: Doorbell
        class: switch
        manufacturer: BTicino
        model: 3476
      hvac_relay_1:
        where: '08'
        name: HVAC relay 1
        class: switch
        manufacturer: Arnould
        model: 64391
```
The configuration is largely the same as lights, except here they cannot be dimmable, and you can specify the `class` if you wish to distinguish between `outlet` and `switch`.  
Here is an opportunity to touch on the `where`; which by OpenWebNet standard needs to be either 2 or 4 digits if you used virtual configuration and went beyond the "usual" numbering.  
It can never be 3 as the bus could not tell if `010` would be "A=01, PL=0" or "A=0, PL=10" for instance.

### Covers
Moving on to WHO 2, covers:

```yaml
cover:
  - platform: myhome
    devices:
      living_shutter:
        where: '11'
        name: Living room shutter
        advanced: True
        manufacturer: Legrand
        model: 67557
      kitchen_shutter:
        where: '12'
        name: Kitchen shutter
        advanced: True
        manufacturer: Legrand
        model: 67557
      dining_room_shutter:
        where: '13'
        name: Dining room shutter
        advanced: True
        manufacturer: Legrand
        model: 67557
```
The configuration remains similar to lights and switches.  
The specificity is the optional `advanced` boolean (defaulting to `False`), you need to set it to `True` if you have 'advanced' cover modules that keep track of and return position values. (Only "Céliane 67557", "Axolute H4661M2", "Livinglight LN4661M2" and the "F401" DIN module are capable of this)

### Binary sensors
Dry contacts and IR sensors are part of WHO 25

```yaml
binary_sensor:
  - platform: myhome
    devices:
      garage_door:
        where: '31'
        name: Garage door
        class: garage_door
        manufacturer: BTicino
        model: 3477
```
`where` for these is one of a few special cases, as per specification, they are always "3" followed by the sensor number assigned "[1-201]".  
`class` allows you to specify any supported Home-Assistant binary sensor `device_class`, this will affect the way the device is presented in the interface.

#### Motion sensors
The "motion" part of the light and motion sensors on WHO 1 are available if the sensor is configured in "scenario" mode. You need to specify the `who`:

```yaml
binary_sensor:
  - platform: myhome
    devices:
      office_motion:
        who: '1'
        where: '0312'
        name: Office
        class: motion
        manufacturer: Legrand
        model: 048822
```
`where` is the OpenWebNet address of your device (the 'APL').  
`who` must be "1" for these sensors.  
`class` must be "motion" for these sensors.

#### Auxiliary sensors
Auxiliary sensors from the alarm system can also be added, you just need to specify the `who`:  

```yaml
binary_sensor:
  - platform: myhome
    devices:
      motion_sensor:
        where: '1'
        who: '9'
        name: Motion living room
        class: motion
        manufacturer: BTicino
        model: L4610
```
`where` is the auxiliary sensor number "[0-9]".  
`who` must be "9" in the case of Auxiliary sensors.  
`class` allows you to specify any supported Home-Assistant binary sensor `device_class`, this will affect the way the device is presented in the interface.

### Heating
Climate entities are developed for WHO 4

There are 3 distinct ways to configure this part depending on your setup:

#### 99 zones central unit

In a 99 zones setup, the central unit needs to have the address `#0`, and all subordinate zones have their own number with and are NOT `standalone`.

```yaml
climate:
  - platform: myhome
    devices:
      central_unit:
        zone: '#0'
        name: Central unit
        heat: True
        cool: False
        standalone: False
        manufacturer: BTicino
        model: 3550
      zone_1:
        zone: '1'
        name: Living room
        heat: True
        cool: False
        standalone: False
        manufacturer: BTicino
        model: F430/4
```
`zone` is the zone (equivalent to `where`)  
`heat` is an optional boolean defaulting to `True` you can set if your installation supports heating  
`cool` is an optional boolean defaulting to `False` you can set if your installation supports cooling  
`standalone` is an optional boolean defaulting to `False`, you can either ignore it or set it manually to `False` when you have a 99 zones setup

#### 4 zones central unit

When you use a 4 zones central unit, the central unit itself acts as a separate zone, so it has a zone number configured; all subordinate zones are `standalone`.

```yaml
climate:
  - platform: myhome
    devices:
      central_unit:
        zone: '1'
        name: Central unit Living room
        heat: True
        cool: False
        central: True
        standalone: False
        manufacturer: BTicino
        model: HC4695
      zone_2:
        zone: '2'
        name: Bedroom
        heat: True
        cool: False
        standalone: True
        manufacturer: BTicino
        model: F430/4
```
`zone` is the zone (equivalent to `where`)  
`heat` is an optional boolean defaulting to `True` you can set if your installation supports heating  
`cool` is an optional boolean defaulting to `False` you can set if your installation supports cooling  
`central`is an optional boolean defaulting to`False` that you need to set to `True` only for the zone that acts as the central unit in your 4 zones setup    
`standalone` is an optional boolean defaulting to `False` that you need to set to `True` for all subordinate zones in a 4 zones setup

#### No central unit

With no central unit, all zones are set as `standalone`.

```yaml
climate:
  - platform: myhome
    devices:
      zone_1:
        zone: '1'
        name: Living room
        heat: True
        cool: False
        standalone: True
        manufacturer: BTicino
        model: H4691
      zone_2:
        zone: '2'
        name: Bedroom
        heat: True
        cool: False
        standalone: True
        manufacturer: BTicino
        model: H4691
```
`zone` is the zone (equivalent to `where`)  
`heat` is an optional boolean defaulting to `True` you can set if your installation supports heating  
`cool` is an optional boolean defaulting to `False` you can set if your installation supports cooling  
`standalone` is an optional boolean defaulting to `False` that you need to set to `True` for all zones when you don't have any central unit

### Sensors
At this point, only energy and temperature sensors are supported as part of WHO 18 and WHO 4:

```yaml
sensor:
  - platform: myhome
    devices:
      general_power:
        where: '51'
        name: Total power
        class: power
        manufacturer: BTicino
        model: F520
      water_heater_power:
        where: '52'
        name: Water heater
        class: power
        manufacturer: BTicino
        model: F520
      washing_machine:
        where: '71'
        name: Washing machine
        class: power
        manufacturer: BTicino
        model: F522
      bedroom_temperature:
        where: '1'
        name: Bedroom temperature
        class: temperature
        manufacturer: BTicino
        model: L4692
      temperature_sensor:
        where: '105'
        name: Secondary sensor
        class: temperature
        manufacturer: BTicino
        model: L4692
      office_illuminance:
        where: '0312'
        name: Office
        class: illuminance
        manufacturer: Legrand
        model: 048822
```
`where` is also a special case for those as well since power meters are always "5" followed by the sensor number assigned "[1-255]" for F520, the address shoud always be "7" followed by the sensor number assigned "[1-255]" for F522.  
`class` is a required element as it will be used to tell apart other types of sensors once implemented, for now `power`, `energy`, `temperature` and `illuminance` are the only admissible values.  

**Note** that you can add secondary temperature sensors (with 3 digits as per OpenWebNet documentation, ie `105` is the 1st 'secondary sensor' of the 5th zone) or main temperature sensor (with 1 or 2 digit being the Zone number).  
*However*, if you add a main zone temperature sensor, *you will not be able to configure a climate entity for that zone* at the same time! You can chose one or the other, if you need both, you should configure the climate entity and then a "template sensor" with the temperature value of the climate entity.  
  
**Note** that illuminance sensors are only available if the sensor itself is configured in "scenario" mode, this is similar to the requirement of the "motion" binary sensor.

### CEN/CEN+ events
A powerful feature is to be able to assign CEN or CEN+ commands to your wall switches and use the events it generates in Home-Assistant to trigger automations.  
CEN/CEN+ devices do not need to be configured in Home-Assistant, as all CEN/CEN+ messages received will alway trigger an event.  
All you need is to create an automation with a trigger like follows:
```yaml
platform: event
event_type: myhome_cenplus_event
event_data:
  event: pushbutton_short_press
  object: 33
  pushbutton: 7
```
or
```yaml
platform: event
event_type: myhome_cen_event
event_data:
  event: pushbutton_long_release
  object: 10
  pushbutton: 1
```
`object` and `pushbutton` are the ones defined in the OpenWebNet CEN or CEN+ configuration.
Supported `events` varies between CEN and CEN+:  
* CEN events:
  * `pushbutton_short_press`
  * `pushbutton_short_release`
  * `pushbutton_long_press`
  * `pushbutton_long_release`  
* CEN+ events:
  * `pushbutton_short_press`
  * `pushbutton_long_press`
  * `pushbutton_long_release`

This is a really useful feature, it allows you to have wall switches turn WLED strips ON and OFF; Play/Pause Skip track on your SONOS... The only limit is your imagination!

### Other events
When  group, area or general commands are detected, they generate events in Home Assistant.  
These events can be used as triggers, so you can for instance trigger an automation when you generate a 'general off'  
Example events use would be:
```yaml
platform: event
event_type: myhome_area_light_event
event_data:
  area: 3
  event: 'on'
```
#### Light events
3 types of light events exist:
* `myhome_general_light_event`
* `myhome_area_light_event`
* `myhome_group_light_event`

All events have an attribute `message` containing the raw OpenWebNet message and an attribute `event` that can be either `on` or `off`.  
Area events also have an attribute `area` containing the area ID (the 'A' of the 'APL'); and Group events have an attribute `group` containing the group ID (without the leading `#`).

#### Automation (cover) events
3 types of cover events exist:
* `myhome_general_automation_event`
* `myhome_area_automation_event`
* `myhome_group_automation_event`

All events have an attribute `message` containing the raw OpenWebNet message and an attribute `event` that can be either `open`, `close` or `stop`.  
Area events also have an attribute `area` containing the area ID (the 'A' of the 'APL'); and Group events have an attribute `group` containing the group ID (without the leading `#`).

### Services
#### Power sensor services
WHO 18 power meters are working in a way that is not always obvious. They will report the "instantaneous power consumption"; but only if requested and only for a given amount of time.  
This means you will periodically need to send a specific command to ask the sensor to start sending its power consumption in real time for a given duration not exceeding 255 minutes.  
This can be done with a service call on the appropriate device.

You can create an automation running on Home-Assistant's startup and every 2 hours to call this service:
```yaml
service: myhome.start_sending_instant_power
data:
  duration: 125
  entity_id: sensor.general_power
```

#### Gateway services
There are very few settings that can be written to the gateway through OpenWebNet, but one of them is the current time.  
You can ensure a form of time synchronization with your gateway by calling the following service on a regular basis:
```yaml
service: myhome.sync_time
data: {}
```
This will write the current system time of your Home-Assistant instance to your gateway.

#### Message sending
Another more general service is used to send an arbitrary message on the bus.  
It can for example be used to send general commands:  
```yaml
service: myhome.send_message
data:
  message: '*1*0*0##'
```