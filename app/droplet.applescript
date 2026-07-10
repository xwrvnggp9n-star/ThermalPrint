-- MXW01 Printer — drag-drop droplet
-- Drop image files onto this app (or double-click to pick files) to print them
-- to the MXW01 Bluetooth thermal printer.

property scriptDir : "/Users/sandy/Projects/mxw01-printer"
property pyBin : "/Users/sandy/Projects/mxw01-printer/.venv/bin/python"

-- Double-clicked (no files): let the user choose images.
on run
	try
		set theItems to (choose file with prompt "Choose image(s) to print on the MXW01:" of type {"public.image"} with multiple selections allowed)
	on error number -128
		return -- user cancelled
	end try
	printItems(theItems)
end run

-- Files dropped onto the app icon.
on open theItems
	printItems(theItems)
end open

on printItems(theItems)
	if (count of theItems) is 0 then return

	-- Build the argument list of POSIX paths.
	set argStr to ""
	repeat with itm in theItems
		set argStr to argStr & " " & quoted form of (POSIX path of itm)
	end repeat

	set pyCmd to quoted form of pyBin
	set scriptPath to quoted form of (scriptDir & "/mxprint.py")
	set cmd to pyCmd & " " & scriptPath & argStr & " 2>&1"

	display notification "Printing " & (count of theItems) & " image(s)…" with title "MXW01 Printer"

	try
		set outp to do shell script cmd
		display notification "Done." with title "MXW01 Printer" subtitle ("Printed " & (count of theItems) & " image(s)")
	on error errMsg number errNum
		set shortMsg to errMsg
		if (count of characters of shortMsg) > 400 then
			set shortMsg to (text 1 thru 400 of shortMsg) & "…"
		end if
		display dialog "Print failed:" & return & return & shortMsg with title "MXW01 Printer" buttons {"OK"} default button "OK" with icon caution
	end try
end printItems
