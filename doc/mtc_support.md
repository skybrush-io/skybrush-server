How to sync the Flockwave server to an external MIDI timecode master
====================================================================

:Author: Tamas Nepusz
:Date: 26 May 2016

This document describes how to sync the Flockwave server to an external MIDI timecode
master device (real or virtual). Work in progress; if something is missing, ask me
and I'll update the documentation as my time allows.

Ubuntu Linux
------------

Common preparations
^^^^^^^^^^^^^^^^^^^

These steps are needed no matter whether you want to sync to an external MTC master
or to a software MTC generator.

1. Make sure that your user is a member of the ``audio`` group, and if not, run
   ``sudo adduser your-user-name audio``.

Syncing to a software MTC generator
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

[Ardour](http://ardour.org) seems to be the de facto DAW (digital audio workstation)
on Linux and it has a built-in MTC generator that we can use. (I haven't found a
standalone MTC generator yet).

Ardour is open source but you are required to pay for it (as much as you want; $1 is
enough) if you use it for an extended amount of time. However, for testing purposes,
we should be okay with the free version.

Installng Ardour is as simple as:

```
sudo add-apt-repository ppa:dobey/audiotools
sudo apt-get update
sudo apt-get install ardour
```

Then we need a virtual MIDI sound card as well; the MIDI timecode will be routed to
this virtual card from Ardour and then the same card will be used as input in
``flockwave-server``:

```
sudo modprobe snd-virmidi          # to create the card now
echo "snd-virmidi" >>/etc/modules  # to ensure that the module is loaded with the next boot
```

Now you can launch Ardour from the command line with ``ardour4``. When you start it
for the first time, it will ask for a directory where new Ardour sessions will be
stored by default, so create an empty folder somewhere and specify that for Ardour.
(I am using ``~/ardour``). It will also ask some additional questions; just accept the
default everywhere, and then create a new session with name ``mtctest``. If it fails
to create the session and shows a message saying that the audio device is not valid,
it sometimes helps to set the audio input device to *None* in the session setup dialog.

After having started the session, we have to instruct Ardour to send MIDI time code
and set up which MIDI output the time code will be routed to:

1. In the *Edit / Preferences* menu, select *MIDI* on the left and tick *Send MIDI
   Time Code*.

2. Select the *Window / Audio/MIDI Setup* menu, and select *ALSA raw devices* in the
   *MIDI System* row. Next to this there is a button labeled *Midi Device Setup*;
   click on that and disable all but one MIDI port whose starts with "VirMIDI".
   Make sure you remember which MIDI port you have left enabled.

3. Select the *Window / MIDI Connections* menu to open the MIDI connection manager,
   then switch to the *Ardour Misc* tab on the left side and to the *Hardware* tab
   at the bottom. Then find the cell in the intersection of the row labeled "MTC out"
   and the column labeled "System", and click the cell to make the connection.

4. Close the MIDI Connection Manager and save the session data to ensure that Ardour
   will remember this setup the next time you open the session.

Next, open the Flockwave server configuration file (e.g., ``flockwave/server/config.py``)
and add a stanza like this to the ``EXTENSIONS`` variable:

```
"smpte_timecode": {
    "connection": "midi:VirMIDI 2-0"
}
```

where ``VirMIDI 2-0`` should be replaced with the VirMIDI port that you have left
enabled in Ardour.

You can now start the Flockwave server and watch the console while you start/stop
the playhead in Ardour. The MIDI timecode in Ardour's main window should be in
sync with the clock named ``mtc`` in the Flockwave server. Watch out for
messages like ``MIDI clock started`` and ``MIDI clock stopped`` in the console
of the Flockwave server when you start and stop the playhead. ``CLK-INF``
messages should also be emitted by the server when you start/stop the playhead
or change its position.
