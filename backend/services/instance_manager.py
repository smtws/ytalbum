#!/usr/bin/env python3
"""
Instance Manager - Handles detection and cleanup of existing backend instances
"""

import psutil
import requests
import asyncio
import signal
import os
import time
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class InstanceManager:
    """Manages YouTube Music Downloader backend instances"""
    
    def __init__(self, port_range_start: int = 3000, port_range_end: int = 9000):
        self.port_range_start = port_range_start
        self.port_range_end = port_range_end
        self.health_timeout = 2.0  # Timeout for health checks
        
    def find_existing_instances(self) -> List[dict]:
        """Find all running YouTube Music Downloader instances"""
        instances = []
        
        # Method 1: Find by process command line
        instances.extend(self._find_by_process())
        
        # Method 2: Find by health endpoint scanning
        instances.extend(self._find_by_health_scan())
        
        # Deduplicate by PID and port
        unique_instances = {}
        for instance in instances:
            key = f"{instance.get('pid', 'unknown')}:{instance.get('port', 'unknown')}"
            if key not in unique_instances:
                unique_instances[key] = instance
                
        return list(unique_instances.values())
    
    def _find_by_process(self) -> List[dict]:
        """Find instances by scanning running processes"""
        instances = []
        
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'connections']):
                try:
                    cmdline = proc.info['cmdline']
                    if not cmdline:
                        continue
                        
                    # Look for our backend process patterns
                    cmdline_str = ' '.join(cmdline)
                    if any([
                        'backend.app:app' in cmdline_str,
                        'youtube-music-downloader' in cmdline_str.lower(),
                        'yt-downloads' in cmdline_str.lower() and 'uvicorn' in cmdline_str,
                        'backend/app.py' in cmdline_str
                    ]):
                        
                        # Try to get port from connections
                        port = self._extract_port_from_process(proc)
                        
                        instances.append({
                            'pid': proc.info['pid'],
                            'name': proc.info['name'],
                            'cmdline': cmdline_str,
                            'port': port,
                            'detection_method': 'process_scan'
                        })
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                    
        except Exception as e:
            logger.warning(f"Error scanning processes: {e}")
            
        return instances
    
    def _extract_port_from_process(self, proc) -> Optional[int]:
        """Extract port number from process connections"""
        try:
            connections = proc.connections(kind='inet')
            for conn in connections:
                if conn.status == psutil.CONN_LISTEN:
                    return conn.laddr.port
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None
    
    def _find_by_health_scan(self) -> List[dict]:
        """Find instances by scanning for health endpoints"""
        instances = []
        
        # Scan localhost/127.0.0.1 for our health endpoints
        hosts = ['127.0.0.1', 'localhost']
        
        for host in hosts:
            for port in range(self.port_range_start, self.port_range_end + 1):
                try:
                    health_url = f"http://{host}:{port}/health"
                    response = requests.get(
                        health_url, 
                        timeout=self.health_timeout,
                        headers={'Accept': 'application/json'}
                    )
                    
                    if response.ok:
                        health_data = response.json()
                        
                        # Verify this is our YouTube downloader backend
                        if (health_data.get('status') == 'healthy' and
                            isinstance(health_data.get('connected_clients'), int) and
                            'current_state' in health_data and
                            'results_count' in health_data):
                            
                            # Try to find the PID for this port
                            pid = self._find_pid_for_port(port)
                            
                            instances.append({
                                'host': host,
                                'port': port,
                                'pid': pid,
                                'health_url': health_url,
                                'health_data': health_data,
                                'detection_method': 'health_scan'
                            })
                            
                            # Only record one instance per port
                            break
                            
                except (requests.RequestException, ValueError, KeyError):
                    continue
                    
        return instances
    
    def _find_pid_for_port(self, port: int) -> Optional[int]:
        """Find the PID of process listening on given port"""
        try:
            for proc in psutil.process_iter(['pid']):
                try:
                    for conn in proc.connections(kind='inet'):
                        if (conn.status == psutil.CONN_LISTEN and 
                            conn.laddr.port == port):
                            return proc.pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return None
    
    def kill_instance(self, instance: dict, force: bool = False) -> bool:
        """Kill a specific instance"""
        pid = instance.get('pid')
        if not pid:
            logger.warning(f"No PID found for instance: {instance}")
            return False
            
        try:
            proc = psutil.Process(pid)
            
            logger.info(f"Terminating instance PID {pid} on port {instance.get('port', 'unknown')}")
            
            if force:
                # Force kill immediately
                proc.kill()
                proc.wait(timeout=3)
            else:
                # Graceful shutdown first
                proc.terminate()
                try:
                    proc.wait(timeout=5)  # Wait up to 5 seconds
                except psutil.TimeoutExpired:
                    logger.warning(f"Process {pid} didn't terminate gracefully, force killing")
                    proc.kill()
                    proc.wait(timeout=3)
                    
            logger.info(f"Successfully terminated instance PID {pid}")
            return True
            
        except psutil.NoSuchProcess:
            logger.info(f"Process {pid} already dead")
            return True
        except psutil.AccessDenied:
            logger.error(f"Access denied when trying to kill process {pid}")
            return False
        except Exception as e:
            logger.error(f"Error killing process {pid}: {e}")
            return False
    
    def kill_all_instances(self, force: bool = False) -> int:
        """Kill all found YouTube Music Downloader instances"""
        instances = self.find_existing_instances()
        
        if not instances:
            logger.info("No existing instances found")
            return 0
            
        logger.info(f"Found {len(instances)} existing instance(s)")
        
        killed_count = 0
        for instance in instances:
            logger.info(f"Instance: PID={instance.get('pid')}, Port={instance.get('port')}, "
                       f"Method={instance.get('detection_method')}")
            
            if self.kill_instance(instance, force=force):
                killed_count += 1
                
        # Give processes time to clean up
        if killed_count > 0:
            time.sleep(2)
            
        logger.info(f"Terminated {killed_count}/{len(instances)} instances")
        return killed_count
    
    def find_free_port(self, preferred_ports: List[int] = None) -> int:
        """Find a free port for the new instance"""
        if preferred_ports is None:
            preferred_ports = [7002, 8000, 8080, 3001, 5000]
            
        # Try preferred ports first
        for port in preferred_ports:
            if self._is_port_free(port):
                return port
                
        # Scan the full range for any free port
        for port in range(self.port_range_start, self.port_range_end + 1):
            if self._is_port_free(port):
                return port
                
        raise RuntimeError(f"No free ports found in range {self.port_range_start}-{self.port_range_end}")
    
    def _is_port_free(self, port: int) -> bool:
        """Check if a port is free"""
        import socket
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                return result != 0  # Port is free if connection fails
        except Exception:
            return True  # Assume free if we can't check
    
    def cleanup_and_find_port(self, preferred_ports: List[int] = None) -> int:
        """Kill existing instances and find a free port"""
        logger.info("Cleaning up existing instances...")
        
        # Kill all existing instances
        killed = self.kill_all_instances(force=False)
        
        # Find a free port
        port = self.find_free_port(preferred_ports)
        
        logger.info(f"Cleanup complete. Killed {killed} instances. Using port {port}")
        return port


def cleanup_existing_instances() -> int:
    """Convenience function to cleanup existing instances and return free port"""
    manager = InstanceManager()
    return manager.cleanup_and_find_port()


if __name__ == "__main__":
    # CLI usage
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    manager = InstanceManager()
    
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        instances = manager.find_existing_instances()
        if instances:
            print(f"Found {len(instances)} existing instances:")
            for i, instance in enumerate(instances, 1):
                print(f"  {i}. PID={instance.get('pid')}, Port={instance.get('port')}, "
                      f"Method={instance.get('detection_method')}")
        else:
            print("No existing instances found")
    else:
        port = cleanup_existing_instances()
        print(f"Cleanup complete. Free port: {port}")