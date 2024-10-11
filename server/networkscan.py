

import conf


from device import create_new_devices, print_scan_stats, save_scanned_devices, update_devices_data_from_scan, exclude_ignored_devices
from helper import timeNowTZ
from logger import mylog
from reporting import skip_repeated_notifications



#===============================================================================
# SCAN NETWORK
#===============================================================================

def process_scan (db):

    # Apply exclusions
    mylog('verbose','[Process Scan]  Exclude ignored devices')     
    exclude_ignored_devices (db)    

    # Load current scan data
    mylog('verbose','[Process Scan]  Processing scan results')     
    save_scanned_devices (db)    

    db.commitDB()
    
    # Print stats
    mylog('none','[Process Scan] Print Stats')
    print_scan_stats(db)
    mylog('none','[Process Scan] Stats end')

    # Create Events    
    mylog('verbose','[Process Scan] Sessions Events (connect / disconnect)')
    insert_events(db)

    # Create New Devices
    # after create events -> avoid 'connection' event
    mylog('verbose','[Process Scan] Creating new devices')
    create_new_devices (db)

    # Update devices info
    mylog('verbose','[Process Scan] Updating Devices Info')
    update_devices_data_from_scan (db)

    # Void false connection - disconnections
    mylog('verbose','[Process Scan] Voiding false (ghost) disconnections')    
    void_ghost_disconnections (db)

    # Pair session events (Connection / Disconnection)
    mylog('verbose','[Process Scan] Pairing session events (connection / disconnection) ')
    pair_sessions_events(db)  
  
    # Sessions snapshot
    mylog('verbose','[Process Scan] Creating sessions snapshot')
    create_sessions_snapshot (db)

    # Sessions snapshot
    mylog('verbose','[Process Scan] Inserting scan results into Online_History')
    insertOnlineHistory(db)
  
    # Skip repeated notifications
    mylog('verbose','[Process Scan] Skipping repeated notifications')
    skip_repeated_notifications (db)

    # Clear current scan as processed 
    # 🐛 CurrentScan DEBUG: comment out below when debugging to keep the CurrentScan table after restarts/scan finishes
    db.sql.execute ("DELETE FROM CurrentScan") 
  
    # Commit changes    
    db.commitDB()

#-------------------------------------------------------------------------------
def void_ghost_disconnections (db):
    sql = db.sql #TO-DO
    startTime = timeNowTZ()
    # Void connect ghost events (disconnect event exists in last X min.) 
    mylog('debug','[Void Ghost Con] - 1 Connect ghost events')
    sql.execute("""UPDATE Events SET eve_PairEventRowid = Null,
                                eve_EventType ='VOIDED - ' || eve_EventType
                            WHERE eve_MAC != 'Internet'
                            AND eve_EventType in ('Connected', 'Down Reconnected')
                            AND eve_DateTime = ?
                            AND eve_MAC IN (
                                SELECT Events.eve_MAC
                                FROM CurrentScan, Devices, Events 
                                WHERE dev_MAC = cur_MAC
                                    AND eve_MAC = cur_MAC
                                    AND eve_EventType = 'Disconnected'
                                    AND eve_DateTime >= DATETIME(?, '-3 minutes')
                                ) """,
                            (startTime, startTime))

    # Void connect paired events
    mylog('debug','[Void Ghost Con] - 2 Paired events')
    sql.execute("""UPDATE Events SET eve_PairEventRowid = Null 
                            WHERE eve_MAC != 'Internet'
                            AND eve_PairEventRowid IN (
                                SELECT Events.RowID
                                FROM CurrentScan, Devices, Events 
                                WHERE dev_MAC = cur_MAC
                                    AND eve_MAC = cur_MAC
                                    AND eve_EventType = 'Disconnected'
                                    AND eve_DateTime >= DATETIME(?, '-3 minutes')
                                ) """,
                            (startTime,))

    # Void disconnect ghost events 
    mylog('debug','[Void Ghost Con] - 3 Disconnect ghost events')
    sql.execute("""UPDATE Events SET eve_PairEventRowid = Null, 
                            eve_EventType = 'VOIDED - '|| eve_EventType
                        WHERE eve_MAC != 'Internet'
                        AND ROWID IN (
                            SELECT Events.RowID
                            FROM CurrentScan, Devices, Events 
                            WHERE dev_MAC = cur_MAC
                                AND eve_MAC = cur_MAC
                                AND eve_EventType = 'Disconnected'
                                AND eve_DateTime >= DATETIME(?, '-3 minutes')
                            ) """,
                (startTime,))

    mylog('debug','[Void Ghost Con] Void Ghost Connections end')

    db.commitDB()

#-------------------------------------------------------------------------------
def pair_sessions_events (db):
    sql = db.sql #TO-DO
   

    # Pair Connection / New Device events
    mylog('debug','[Pair Session] - 1 Connections / New Devices')
    sql.execute ("""UPDATE Events
                    SET eve_PairEventRowid =
                       (SELECT ROWID
                        FROM Events AS EVE2
                        WHERE EVE2.eve_EventType IN ('New Device', 'Connected', 'Down Reconnected',
                            'Device Down', 'Disconnected')
                           AND EVE2.eve_MAC = Events.eve_MAC
                           AND EVE2.eve_Datetime > Events.eve_DateTime
                        ORDER BY EVE2.eve_DateTime ASC LIMIT 1)
                    WHERE eve_EventType IN ('New Device', 'Connected', 'Down Reconnected')
                    AND eve_PairEventRowid IS NULL
                 """ )

    # Pair Disconnection / Device Down
    mylog('debug','[Pair Session] - 2 Disconnections')
    sql.execute ("""UPDATE Events
                    SET eve_PairEventRowid =
                        (SELECT ROWID
                         FROM Events AS EVE2
                         WHERE EVE2.eve_PairEventRowid = Events.ROWID)
                    WHERE eve_EventType IN ('Device Down', 'Disconnected')
                      AND eve_PairEventRowid IS NULL
                 """ )
    mylog('debug','[Pair Session] Pair session end')

    db.commitDB()

#-------------------------------------------------------------------------------
def create_sessions_snapshot (db):
    sql = db.sql #TO-DO

    # Clean sessions snapshot
    mylog('debug','[Sessions Snapshot] - 1 Clean')
    sql.execute ("DELETE FROM SESSIONS" )

    # Insert sessions
    mylog('debug','[Sessions Snapshot] - 2 Insert')
    sql.execute ("""INSERT INTO Sessions
                    SELECT * FROM Convert_Events_to_Sessions""" )

    mylog('debug','[Sessions Snapshot] Sessions end')
    db.commitDB()


#-------------------------------------------------------------------------------
def insert_events (db):
    sql = db.sql #TO-DO
    startTime = timeNowTZ()    
    
    # Check device down
    mylog('debug','[Events] - 1 - Devices down')
    sql.execute (f"""INSERT INTO Events (eve_MAC, eve_IP, eve_DateTime,
                        eve_EventType, eve_AdditionalInfo,
                        eve_PendingAlertEmail)
                    SELECT dev_MAC, dev_LastIP, '{startTime}', 'Device Down', '', 1
                    FROM Devices 
                    WHERE dev_AlertDeviceDown != 0
                      AND dev_PresentLastScan = 1                      
                      AND NOT EXISTS (SELECT 1 FROM CurrentScan
                                      WHERE dev_MAC = cur_MAC
                                         ) """)

    # Check new Connections or Down Reconnections
    mylog('debug','[Events] - 2 - New Connections')
    sql.execute (f"""    INSERT INTO Events (eve_MAC, eve_IP, eve_DateTime,
                                            eve_EventType, eve_AdditionalInfo,
                                            eve_PendingAlertEmail)
                        SELECT DISTINCT c.cur_MAC, c.cur_IP, '{startTime}', 
                                        CASE 
                                            WHEN last_event.eve_EventType = 'Device Down' and  last_event.eve_PendingAlertEmail = 0 THEN 'Down Reconnected' 
                                            ELSE 'Connected' 
                                        END,
                                        '',
                                        1
                        FROM LatestEventsPerMAC AS d 
                        JOIN CurrentScan AS c ON d.dev_MAC = c.cur_MAC
                        LEFT JOIN LatestEventsPerMAC AS last_event ON d.dev_MAC = last_event.eve_MAC 
                        WHERE d.dev_PresentLastScan = 0   
                        """)

    # Check disconnections
    mylog('debug','[Events] - 3 - Disconnections')
    sql.execute (f"""INSERT INTO Events (eve_MAC, eve_IP, eve_DateTime,
                        eve_EventType, eve_AdditionalInfo,
                        eve_PendingAlertEmail)
                    SELECT dev_MAC, dev_LastIP, '{startTime}', 'Disconnected', '',
                        dev_AlertEvents
                    FROM Devices
                    WHERE dev_AlertDeviceDown = 0
                      AND dev_PresentLastScan = 1
                      AND NOT EXISTS (SELECT 1 FROM CurrentScan
                                      WHERE dev_MAC = cur_MAC
                                         ) """)

    # Check IP Changed
    mylog('debug','[Events] - 4 - IP Changes')
    sql.execute (f"""INSERT INTO Events (eve_MAC, eve_IP, eve_DateTime,
                        eve_EventType, eve_AdditionalInfo,
                        eve_PendingAlertEmail)
                    SELECT cur_MAC, cur_IP, '{startTime}', 'IP Changed',
                        'Previous IP: '|| dev_LastIP, dev_AlertEvents
                    FROM Devices, CurrentScan
                    WHERE dev_MAC = cur_MAC                        
                      AND dev_LastIP <> cur_IP """ )
    mylog('debug','[Events] - Events end')
    
    
#-------------------------------------------------------------------------------
def insertOnlineHistory(db):
    sql = db.sql  # TO-DO: Implement sql object

    scanTimestamp = timeNowTZ()

    # Query to fetch all relevant device counts in one go
    query = """
    SELECT
        COUNT(*) AS allDevics,
        SUM(CASE WHEN dev_Archived = 1 THEN 1 ELSE 0 END) AS archivedDevices,
        SUM(CASE WHEN dev_PresentLastScan = 1 THEN 1 ELSE 0 END) AS onlineDevices,
        SUM(CASE WHEN dev_PresentLastScan = 0 AND dev_AlertDeviceDown = 1 THEN 1 ELSE 0 END) AS downDevices
    FROM Devices
    """
    
    deviceCounts = db.read(query)[0]  # Assuming db.read returns a list of rows, take the first (and only) row

    allDevics = deviceCounts['allDevics']
    archivedDevices = deviceCounts['archivedDevices']
    onlineDevices = deviceCounts['onlineDevices']
    downDevices = deviceCounts['downDevices']
    
    offlineDevices = allDevics - archivedDevices - onlineDevices

    # Prepare the insert query using parameterized inputs
    insert_query = """
        INSERT INTO Online_History (Scan_Date, Online_Devices, Down_Devices, All_Devices, Archived_Devices, Offline_Devices)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    
    mylog('debug', f'[Presence graph] Sql query: {insert_query} with values: {scanTimestamp}, {onlineDevices}, {downDevices}, {allDevics}, {archivedDevices}, {offlineDevices}')

    # Insert the gathered data into the history table
    sql.execute(insert_query, (scanTimestamp, onlineDevices, downDevices, allDevics, archivedDevices, offlineDevices))

    db.commitDB()


