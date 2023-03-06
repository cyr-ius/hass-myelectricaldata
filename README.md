# Enedis gateway
This a *custom component* for [Home Assistant](https://www.home-assistant.io/). 

With Enedis, get datas from [MyElectricalData](https://myelectricaldata.fr)

There is currently support for the following device types within Home Assistant:
* [Power_sensor](#sensor)



### HACS [![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
Once HACS is installed,

Click on the 3 dots at the top right

Add custom repositories

    Integration : https://github.com/Cyr-ius/hass-myelectricaldata

Add install Enedis [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=enedis)

## Features

- Supports the consumption and production of Linky meters

- Supports multiple billing ranges in consumption mode.

- Possibility of defining the cost of tariffs on the energy produced, consumed, peak hours, off-peak hours.

- Possibility to add a specific price on a time slot

![GitHub release](https://img.shields.io/github/release/Cyr-ius/hass-myelectricaldata)


## Configuration

The preferred way to setup the platform is by enabling the discovery component.
Add your equipment via the Integration menu

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=myelectricaldata)



### Within the Options menu:

The ranges must be set from midnight to midnight. (00H00 - 00H00)
A range that has the same billing must have the same name so that HA can perform the sum.
On each beach, it is necessary to add an hourly rate

Example :
My contract stipulates off-peak hours from 01H30 to 08H00 and from 12H30 to 14H30.

The following ranges should be defined:

    Peak hours 00H00 01H30 0.12
    Off-peak hours 01H00 08H00 0.08
    Peak hours 08H00 12:30 0.12
    Off-peak hours 12H30 14H00 0.08
    Peak hours 14H00 00H00 0.12


![image](https://user-images.githubusercontent.com/1258123/194921798-3bf9a495-e1af-4291-93ef-d5bb4783535c.png)





