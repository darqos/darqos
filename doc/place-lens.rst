Place Lens
==========

This is a fairly complex lens, due to the variety of ways that it's
possible to describe a place.

* Planetary geographical coordinates: longitude, latitude, altitude.  And a planet, of course.
* Political entity addresses (country, city, street, land block)
* Containment relationships (shelf 3, case 1, living room, house at address)
* Solar-relative coordinates (angle relative to orbital plane, distance)

The most appropriate presentation will depending upon the description.

* A street map is probably best for street addresses
* A packed boxes diagram for containment sequences
* Perhaps a globe and place market for geographical coordinates?
* A solar-system map for solar-relative coordinates

  * Similar for galaxy-relative coordinates.
  * And maybe a general star map for ICRS coordinates

The most common system will almost certainly be an Earth-based street
or topographical map.  This could come from OpenStreetMap (for
streets), but I'm not sure for topo data (like, for bike rides).

A containment map will likely top-out at a street address.  Once
zoomed in past there, a virtuual maps of contained boxes would work,
but it'd be ideal to have a physical layout for stuff at home: floor
plan, etc.

Consider a book shelf:
* at an address
* Could be multi-storey
* A 2D floor plan for each floor

  * Some houses have mezzanines and voids and sunken areas, and such
    that don't work super well with a 2D floor plan.
  * Maybe it's better to just jump straight into a 3D model?

* Within a floor (or a 3D building), you could have a bookcase.
* A bookcase would be trivially divided into rectangular "shelf"
  volumes.
* Ther ecould be more granular locations of an envelope in a box on a
  shelf.
* In the Book lens, it should launch a Place lens or browser to specify the place.

  * "Recent places" list will be really useful
  * Places will just be ItemIDs so far as Book is concerned.  The
    Place chooser should return an ItemID.

Street or Topographical Map
---------------------------

View shows the place marker with a bit of context.  You can zoom in or
out.  You can have an overhead or pseudo-3D view.  If you hover at a
place, it might be reasonable to fetch StreetView-style imagery?  What
does OSM offer here?\

Editing the location allows you to drag the marker.  As it is dragged,
it should show a preview of the returned location info: a street
address, or lat/long/alt coordinates.

Containment
-----------

Can we get house height from OSM?  Ideally we'd have a 3D model of the
building associated with the street address or coordinates.

That 3D model should have minimal texturing, but ideally would map the
place.  It's likely only to be the operator's house(s), maybe
workplace(s), and the houses of closely related people.  Perhaps a
workshed, clubhouse, or a small number of other structures.

Should the 3D model be a separate type?  It almost has to be: the
location is just a sequence of containments.

  Box 1 -> Shelf Z -> Cupboard Y -> Room X -> 23 Example Ave, Town, State, Country

Each containment is itself a separate place, so the final location is
just an ItemID, but the Lens needs to follow the hierachy.  Attaching
a model to this is like Open Street Map -- it's an external item to
which you can map the identity of the locations.
