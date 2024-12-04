import serial
import time
from typing import Optional
import json
import os
from pathlib import Path

class ChemyxPump:
    def __init__(self, port: str, baudrate: int = 38400):
        """Initialize connection to Chemyx pump.
        
        Args:
            port: Serial port (e.g., '/dev/tty.usbserial-XXXXX' for Mac)
            baudrate: Communication speed (usually 38400 or 9600)
        """
        self.port = port
        self.baudrate = baudrate
        self.connection: Optional[serial.Serial] = None
        self.min_rate: Optional[float] = None
        self.max_rate: Optional[float] = None
        self.min_volume: Optional[float] = None
        self.max_volume: Optional[float] = None
        self.current_units = 0  # Default to mL/min (0)
        
        # Unit conversion factors
        self.UNIT_CONVERSIONS = {
            0: {'name': 'mL/min', 'factor': 1.0},      # mL/min (base unit)
            1: {'name': 'mL/hr',  'factor': 1/60.0},   # mL/hr to mL/min
            2: {'name': 'μL/min', 'factor': 0.001},    # μL/min to mL/min
            3: {'name': 'μL/hr',  'factor': 0.001/60}  # μL/hr to mL/min
        }
        
    def connect(self) -> bool:
        """Establish connection to the pump."""
        try:
            self.connection = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1,
                bytesize=8,
                parity='N',
                stopbits=1
            )
            
            # Clear any stuck responses
            self.clear_communication()
            
            # Verify connection
            if self.connection.is_open:
                # Send a test command (status request)
                test_response = self.send_command("status")
                print(f"Connection test response: {test_response}")
                
                # Get parameter limits
                if self.parse_limits():
                    print(f"Parameter limits loaded: Rate ({self.min_rate} to {self.max_rate} mL/min)")
                    print(f"                        Volume ({self.min_volume} to {self.max_volume} mL)")
                else:
                    print("Warning: Could not load parameter limits")
                
                return True
            return False
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
            
    def disconnect(self):
        """Safely disconnect from pump."""
        if self.connection and self.connection.is_open:
            self.connection.close()
            
    def send_command(self, command: str) -> str:
        """Send command to pump and get response."""
        if not self.connection or not self.connection.is_open:
            return "Error: Not connected to pump"
        
        try:
            # Clear input buffer first
            self.connection.reset_input_buffer()
            
            # Add carriage return if not present
            if not command.endswith('\r'):
                command += '\r'
            
            # Convert string to bytes and send
            self.connection.write(command.encode())
            self.connection.flush()  # Ensure the command is sent
            
            # Wait briefly for the pump to process
            time.sleep(0.2)
            
            # Read all available responses
            responses = []
            while self.connection.in_waiting:
                response = self.connection.readline().decode().strip()
                if response:
                    responses.append(response)
                    time.sleep(0.1)  # Small delay between reads
            
            # If no response, try one more read
            if not responses:
                response = self.connection.readline().decode().strip()
                if response:
                    responses.append(response)
            
            # Get the final response (ignoring echoes and prompts)
            final_response = None
            for response in responses:
                if not response.startswith('>') and not response.startswith(command.strip()):
                    final_response = response
            
            if final_response:
                print(f"Debug - Sent: {command.strip()}, Received: {final_response}")
                return final_response
            else:
                print(f"Debug - Sent: {command.strip()}, No valid response received")
                return "No response"
            
        except Exception as e:
            return f"Command failed: {e}"
            
    def set_rate(self, rate: float) -> str:
        """Set flow rate in current units."""
        try:
            # Convert input rate to base units (mL/min) for limit checking
            rate_ml_min = self.convert_to_base_units(rate, self.current_units)
            
            # Check against stored limits (which are in mL/min)
            if self.min_rate is not None and self.max_rate is not None:
                if rate_ml_min < self.min_rate or rate_ml_min > self.max_rate:
                    # Convert limits to current units for error message
                    min_rate_curr = self.convert_from_base_units(self.min_rate, self.current_units)
                    max_rate_curr = self.convert_from_base_units(self.max_rate, self.current_units)
                    unit_name = self.UNIT_CONVERSIONS[self.current_units]['name']
                    return f"Rate {rate} is outside pump limits ({min_rate_curr:.4f} to {max_rate_curr:.4f} {unit_name})"
            
            # Format rate to 4 decimal places
            formatted_rate = "{:.4f}".format(float(rate))
            return self.send_command(f"set rate {formatted_rate}")
        
        except ValueError as e:
            return f"Invalid rate value: {e}"
        
    def set_volume(self, volume: float, mode: str = "infusion") -> str:
        """Set volume in mL.
        Args:
            volume: Volume in mL
            mode: Either "infusion" (positive) or "withdrawal" (negative)
        """
        try:
            # Convert to absolute value and format to 4 decimal places
            vol = float(abs(volume))
            if mode == "withdrawal":
                vol = -vol
            
            # Format with 4 decimal places to avoid floating point issues
            formatted_vol = "{:.4f}".format(vol)
            
            # Check against stored limits
            if self.min_volume is not None and self.max_volume is not None:
                if abs(vol) < self.min_volume or abs(vol) > self.max_volume:
                    return f"Volume {abs(vol)} is outside pump limits ({self.min_volume} to {self.max_volume} mL)"
            
            return self.send_command(f"set volume {formatted_vol}")
        except ValueError as e:
            return f"Invalid volume value: {e}"
        
    def set_diameter(self, diameter: float) -> str:
        """Set syringe diameter in mm."""
        return self.send_command(f"set diameter {diameter}")
        
    def start(self) -> str:
        """Start the pump."""
        return self.send_command("start")
        
    def stop(self) -> str:
        """Stop the pump."""
        return self.send_command("stop")
        
    def get_status(self) -> str:
        """Get pump status.
        Returns:
            0: Pump stopped
            1: Pump running
            2: Pump paused
            3: Pump delayed
            4: Pump stalled
        """
        return self.send_command("status")

    def set_all_parameters(self, diameter: float, volume: float, 
                         rate: float, delay: float = 0, 
                         mode: str = "infusion", start_immediately: bool = False) -> str:
        """Set all parameters in one command using hexw2.
        
        Args:
            diameter: Syringe diameter in mm
            volume: Volume in mL
            rate: Flow rate in mL/min
            delay: Delay in minutes (default 0)
            mode: "infusion" (0) or "withdrawal" (1)
            start_immediately: Whether to start pump after setting parameters
        """
        mode_val = 1 if mode == "withdrawal" else 0
        command = f"hexw2 0 {mode_val} {diameter} {abs(volume)} {rate} {delay}"
        if start_immediately:
            command += " start"
        return self.send_command(command)

    def get_parameters(self) -> str:
        """Get current parameter settings."""
        return "Use 'view <parameter>' to view specific parameters (e.g., 'view rate', 'view volume')"

    def set_units(self, unit_code: int) -> str:
        """Set flow rate units.
        Args:
            unit_code: Integer 0-3 where:
                0 = mL/min
                1 = mL/hr
                2 = μL/min
                3 = μL/hr
        """
        if unit_code not in range(4):
            return "Error: Unit code must be 0-3"
        response = self.send_command(f"set units {unit_code}")
        if not response.startswith("Error"):
            self.current_units = unit_code
        return response

    def set_time(self, time: float) -> str:
        """Set target time for pump run (in minutes)."""
        return self.send_command(f"set time {time}")

    def set_delay(self, delay: float) -> str:
        """Set start time delay (in minutes)."""
        return self.send_command(f"set delay {delay}")

    def set_prime_rate(self, rate: float) -> str:
        """Set priming/bolus rate."""
        return self.send_command(f"set primerate {rate}")

    def pause(self) -> str:
        """Pause the current pump run."""
        return self.send_command("pause")

    def get_limits(self) -> str:
        """Get min/max values for volume and rate."""
        return self.send_command("read limit parameter")

    def get_dispensed_volume(self) -> str:
        """Get transferred volume for current/last run."""
        return self.send_command("dispensed volume")

    def get_elapsed_time(self) -> str:
        """Get elapsed time for current/last run."""
        return self.send_command("elapsed time")

    def restart(self) -> str:
        """Power cycle the pump."""
        return self.send_command("restart")

    def clear_communication(self):
        """Clear any stuck responses in the communication buffer."""
        if not self.connection or not self.connection.is_open:
            return
        
        try:
            self.connection.reset_input_buffer()
            self.connection.reset_output_buffer()
            time.sleep(0.5)  # Wait for buffers to clear
        except Exception as e:
            print(f"Failed to clear communication: {e}")

    def save_config(self, filename: str) -> str:
        """Save current pump configuration to a JSON file."""
        if not filename.endswith('.json'):
            filename += '.json'
        
        try:
            # Get current parameters
            config = {
                "info": {
                    "description": "Saved pump configuration",
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "notes": "Automatically saved configuration"
                },
                "commands": []
            }
            
            # Get current values
            diameter = self.send_command("read diameter parameter")
            volume = self.send_command("read volume parameter")
            rate = self.send_command("read rate parameter")
            delay = self.send_command("read delay parameter")
            prime = self.send_command("read primerate parameter")
            
            # Parse and add commands
            for response in [diameter, volume, rate, delay, prime]:
                if ":" in response:
                    param, value = response.split(":", 1)
                    param = param.strip().lower()
                    value = value.strip().split()[0]  # Remove units
                    config["commands"].append(f"{param} {value}")
            
            # Save to file
            with open(filename, 'w') as f:
                json.dump(config, f, indent=4)
            
            return f"Configuration saved to {filename}"
        
        except Exception as e:
            return f"Failed to save configuration: {e}"

    def load_config(self, filename: str) -> str:
        """Load pump configuration from a JSON file and apply it."""
        if not filename.endswith('.json'):
            filename += '.json'
        
        try:
            with open(filename, 'r') as f:
                config = json.load(f)
            
            responses = []
            
            # Handle both old and new config formats
            if "commands" in config:
                # New format with commands list
                for cmd in config["commands"]:
                    parts = cmd.split()
                    command = parts[0].lower()
                    value = float(parts[1]) if len(parts) > 1 else None
                    
                    if command == "diameter":
                        responses.append(self.set_diameter(value))
                    elif command == "volume":
                        mode = "withdrawal" if value < 0 else "infusion"
                        responses.append(self.set_volume(abs(value), mode))
                    elif command == "rate":
                        responses.append(self.set_rate(value))
                    elif command == "delay":
                        responses.append(self.set_delay(value))
                    elif command == "prime":
                        responses.append(self.set_prime_rate(value))
                    elif command == "start":
                        responses.append(self.start())
                    time.sleep(0.1)
            else:
                # Old format with parameter dictionary
                for param, value in config.items():
                    if param == 'diameter':
                        responses.append(self.set_diameter(value))
                    elif param == 'volume':
                        mode = "withdrawal" if value < 0 else "infusion"
                        responses.append(self.set_volume(abs(value), mode))
                    elif param == 'rate':
                        responses.append(self.set_rate(value))
                    elif param == 'delay':
                        responses.append(self.set_delay(value))
                    elif param == 'time':
                        responses.append(self.set_time(value))
                    elif param == 'units':
                        responses.append(self.set_units(int(value)))
                    time.sleep(0.1)
            
            return f"Configuration loaded from {filename}\nResponses: {'; '.join(responses)}"
        
        except FileNotFoundError:
            return f"Configuration file {filename} not found"
        except Exception as e:
            return f"Failed to load configuration: {e}"

    def view_parameter(self, param: str) -> str:
        """View a specific parameter.
        
        Args:
            param: Parameter to view (e.g., 'diameter', 'rate', 'volume', 'delay', etc.)
        """
        # First try reading the parameter directly
        response = self.send_command(f"read {param}")
        
        # If that doesn't work, try with "parameter"
        if "command list" in response.lower():
            response = self.send_command(f"read {param} parameter")
        
        return response

    def parse_limits(self) -> bool:
        """Parse and store pump parameter limits.
        Format: max_rate min_rate max_volume min_volume
        Returns:
            bool: True if limits were successfully parsed
        """
        try:
            limits_response = self.get_limits()
            parts = limits_response.split()
            
            if len(parts) >= 4:
                # Response format: max_rate min_rate max_volume min_volume
                self.max_rate = float(parts[0])
                self.min_rate = float(parts[1])
                self.max_volume = float(parts[2])
                self.min_volume = float(parts[3])
                
                # Sanity check - sometimes the values might need to be swapped
                if self.min_rate > self.max_rate:
                    self.min_rate, self.max_rate = self.max_rate, self.min_rate
                if self.min_volume > self.max_volume:
                    self.min_volume, self.max_volume = self.max_volume, self.min_volume
                    
                return True
                
            return False
        except Exception as e:
            print(f"Failed to parse limits: {e}")
            return False

    def convert_to_base_units(self, value: float, from_units: int) -> float:
        """Convert a value from given units to base units (mL/min)"""
        return value * self.UNIT_CONVERSIONS[from_units]['factor']

    def convert_from_base_units(self, value: float, to_units: int) -> float:
        """Convert a value from base units (mL/min) to desired units"""
        return value / self.UNIT_CONVERSIONS[to_units]['factor']

    def get_current_units(self) -> str:
        """Get the current unit settings."""
        if self.current_units in self.UNIT_CONVERSIONS:
            return self.UNIT_CONVERSIONS[self.current_units]['name']
        return "unknown"

def main():
    # Replace with the new port you found
    PORT = '/dev/tty.usbserial-AQ00FZVU'  # Use your actual port name
    
    # Create pump instance
    pump = ChemyxPump(PORT)
    
    if not pump.connect():
        print("Failed to connect to pump. Please check connection and port.")
        return
        
    print("Connected to pump. Type 'help' for commands, 'exit' to quit.")
    
    # Updated command help dictionary
    commands = {
        'rate': 'Set flow rate. Usage: rate <value>',
        'volume': 'Set volume (mL). Usage: volume <value>',
        'diameter': 'Set syringe diameter (mm). Usage: diameter <value>',
        'units': 'Set flow rate units. Usage: units <code>\n' +
                '       0=mL/min, 1=mL/hr, 2=μL/min, 3=μL/hr',
        'time': 'Set target time (minutes). Usage: time <value>',
        'delay': 'Set start delay (minutes). Usage: delay <value>',
        'prime': 'Set priming/bolus rate. Usage: prime <rate>',
        'start': 'Start the pump',
        'pause': 'Pause the current run',
        'stop': 'Stop the pump',
        'restart': 'Power cycle the pump',
        'status': 'Get pump status (0=stopped, 1=running, 2=paused, 3=delayed, 4=stalled)',
        'limits': 'Show min/max values for volume and rate',
        'dispensed': 'Show transferred volume for current/last run',
        'elapsed': 'Show elapsed time for current/last run',
        'config': 'Set all parameters at once.\n' +
                 '       Usage: config <diameter> <volume> <rate> [delay] [mode] [start]\n' +
                 '       mode: "infusion" or "withdrawal"\n' +
                 '       delay: in minutes (optional, default 0)\n' +
                 '       start: "true" or "false" (optional)\n' +
                 '       Example: config 23.04 1.2 3.0 0 infusion true',
        'view': 'View current parameter settings',
        'help': 'Show this help message',
        'exit': 'Exit the program',
        'clear': 'Clear communication buffer',
        'save': 'Save current configuration to file. Usage: save <filename>',
        'load': 'Load configuration from file. Usage: load <filename>',
        'list': 'List saved configuration files',
    }
    
    while True:
        try:
            command = input("> ").strip().lower()
            
            if command == 'exit':
                break
            elif command == 'help':
                for cmd, desc in commands.items():
                    print(f"{cmd}: {desc}")
            elif command.startswith('rate '):
                rate = float(command.split()[1])
                print(pump.set_rate(rate))
            elif command.startswith('volume '):
                volume = float(command.split()[1])
                print(pump.set_volume(volume))
            elif command.startswith('diameter '):
                diameter = float(command.split()[1])
                print(pump.set_diameter(diameter))
            elif command.startswith('config '):
                parts = command.split()[1:]  # Split and remove 'config' command
                if len(parts) < 3:
                    print("Error: config requires at least diameter, volume, and rate")
                    continue
                
                # Required parameters
                diameter = float(parts[0])
                volume = float(parts[1])
                rate = float(parts[2])
                
                # Optional parameters with defaults
                delay = float(parts[3]) if len(parts) > 3 else 0
                mode = parts[4] if len(parts) > 4 else "infusion"
                start_immediately = parts[5].lower() == "true" if len(parts) > 5 else False
                
                print(pump.set_all_parameters(
                    diameter=diameter,
                    volume=volume,
                    rate=rate,
                    delay=delay,
                    mode=mode,
                    start_immediately=start_immediately
                ))
            elif command.startswith('units '):
                unit_code = int(command.split()[1])
                print(pump.set_units(unit_code))
                print(f"Current units: {pump.get_current_units()}")
            elif command.startswith('time '):
                time_val = float(command.split()[1])
                print(pump.set_time(time_val))
            elif command.startswith('delay '):
                delay = float(command.split()[1])
                print(pump.set_delay(delay))
            elif command.startswith('prime '):
                rate = float(command.split()[1])
                print(pump.set_prime_rate(rate))
            elif command == 'pause':
                print(pump.pause())
            elif command == 'restart':
                print(pump.restart())
            elif command == 'limits':
                print(pump.get_limits())
            elif command == 'dispensed':
                print(pump.get_dispensed_volume())
            elif command == 'elapsed':
                print(pump.get_elapsed_time())
            elif command == 'start':
                print(pump.start())
            elif command == 'stop':
                print(pump.stop())
            elif command == 'status':
                print(pump.get_status())
            elif command.startswith('view '):
                param = command.split(maxsplit=1)[1]
                print(pump.view_parameter(param))
            elif command == 'view':
                print(pump.get_parameters())
            elif command == 'clear':
                print(pump.clear_communication())
            elif command.startswith('save '):
                filename = command.split(maxsplit=1)[1]
                print(pump.save_config(filename))
            elif command.startswith('load '):
                filename = command.split(maxsplit=1)[1]
                print(pump.load_config(filename))
            elif command == 'list':
                configs = [f for f in os.path(os.listdir() + "config") if f.endswith('.json')]
                if configs:
                    print("Available configurations:")
                    for config in configs:
                        print(f"  - {config}")
                else:
                    print("No configuration files found")
            else:
                print("Unknown command. Type 'help' for available commands.")
                
        except ValueError:
            print("Invalid number format")
        except IndexError:
            print("Missing parameter")
        except Exception as e:
            print(f"Error: {e}")
    
    pump.disconnect()
    print("Disconnected from pump")

if __name__ == "__main__":
    main()
