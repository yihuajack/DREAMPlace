import math
import torch
from torch import nn
import torch.nn.functional as F

import dreamplace.ops.adjust_node_area.adjust_node_area_cpp as adjust_node_area_cpp
import dreamplace.ops.adjust_node_area.update_pin_offset_cpp as update_pin_offset_cpp
try:
    import dreamplace.ops.adjust_node_area.adjust_node_area_cuda as adjust_node_area_cuda
    import dreamplace.ops.routability.update_pin_offset_cuda as update_pin_offset_cuda
except:
    pass

class AdjustNodeAreaFromRouteMap(nn.Module):
    def __init__(self,
            xl,
            yl,
            xh,
            yh,
            num_movable_nodes,
            num_bins_x,
            num_bins_y,
            num_threads=8
            ):
        super(AdjustNodeAreaFromRouteMap, self).__init__()
        self.xl = xl 
        self.yl = yl 
        self.xh = xh 
        self.yh = yh 
        self.num_movable_nodes = num_movable_nodes 
        self.num_bins_x = num_bins_x 
        self.num_bins_y = num_bins_y 
        self.bin_size_x = (xh - xl) / num_bins_x 
        self.bin_size_y = (yh - yl) / num_bins_y 
        self.num_threads = num_threads 

    def forward(self, pos, node_size_x, node_size_y, utilization_map):
        if pos.is_cuda:
            output = adjust_node_area_cuda.forward(
                    pos,
                    node_size_x,
                    node_size_y,
                    utilization_map,
                    self.bin_size_x,
                    self.bin_size_y,
                    self.xl,
                    self.yl,
                    self.xh,
                    self.yh,
                    self.num_movable_nodes,
                    self.num_bins_x,
                    self.num_bins_y,
                    )
        else:
            output = adjust_node_area_cpp.forward(
                    pos,
                    self.node_size_x,
                    self.node_size_y,
                    utilization_map,
                    self.bin_size_x,
                    self.bin_size_y,
                    self.xl,
                    self.yl,
                    self.xh,
                    self.yh,
                    self.num_movable_nodes,
                    self.num_bins_x,
                    self.num_bins_y,
                    self.num_threads
                    )
        return output 

class AdjustNodeAreaFromPinMap(AdjustNodeAreaFromRouteMap):
    def __init__(self,
            pin_weights, 
            flat_node2pin_start_map, 
            xl,
            yl,
            xh,
            yh,
            num_movable_nodes,
            num_bins_x,
            num_bins_y,
            unit_pin_capacity, 
            num_threads=8
            ):
        super(AdjustNodeAreaFromPinMap, self).__init__(
                xl, yl, xh, yh, 
                num_movable_nodes, 
                num_bins_x, num_bins_y, 
                num_threads
                )
        self.unit_pin_capacity = unit_pin_capacity
        # for each physical node, we use the pin counts as the weights
        if pin_weights is not None:
            self.pin_weights = pin_weights 
        elif flat_node2pin_start_map is not None: 
            self.pin_weights = (flat_node2pin_start_map[1:self.num_movable_nodes + 1] - flat_node2pin_start_map[:self.num_movable_nodes]).to(self.node_size_x.dtype)
        else:
            assert "either pin_weights or flat_node2pin_start_map is required"

    def forward(self, pos, node_size_x, node_size_y, utilization_map):
        output = super(AdjustNodeAreaFromPinMap, self).forward(
                pos, 
                node_size_x, 
                node_size_y, 
                utilization_map)
        output.mul_(self.pin_weights / (node_size_x * node_size_y * self.unit_pin_capacity))
        return output 


class AdjustNodeArea(nn.Module):
    def __init__(self,
            flat_node2pin_start_map, flat_node2pin_map,
            pin_weights, flat_node2pin_start_map, # only one of them needed 
            xl,
            yl,
            xh,
            yh,
            num_movable_nodes, num_filler_nodes, 
            route_num_bins_x,
            route_num_bins_y,
            pin_num_bins_x,
            pin_num_bins_y,
            area_adjust_stop_ratio=0.01,
            route_area_adjust_stop_ratio=0.01,
            pin_area_adjust_stop_ratio=0.05,
            unit_pin_capacity=0.0,
            num_threads=8
            ):
        super(AdjustInstanceArea, self).__init__()
        self.flat_node2pin_start_map = flat_node2pin_start_map 
        self.flat_node2pin_map = flat_node2pin_map 
        self.xl = xl
        self.xh = xh
        self.yl = yl
        self.yh = yh

        self.num_movable_nodes = num_movable_nodes
        self.num_filler_nodes = num_filler_nodes 
        self.num_threads = num_threads

        # stop ratio
        self.area_adjust_stop_ratio = area_adjust_stop_ratio
        self.route_area_adjust_stop_ratio = route_area_adjust_stop_ratio
        self.pin_area_adjust_stop_ratio = pin_area_adjust_stop_ratio

        # route_opt_area param
        self.route_num_bins_x = route_num_bins_x
        self.route_num_bins_y = route_num_bins_y
        self.route_bin_size_x = (xh - xl) / self.route_num_bins_x
        self.route_bin_size_y = (yh - yl) / self.route_num_bins_y

        # pin_opt_area param
        self.pin_num_bins_x = pin_num_bins_x
        self.pin_num_bins_y = pin_num_bins_y
        self.pin_bin_size_x = (xh - xl) / self.pin_num_bins_x
        self.pin_bin_size_y = (yh - yl) / self.pin_num_bins_y
        self.unit_pin_capacity = unit_pin_capacity

        self.adjust_node_area_route = AdjustNodeAreaFromRouteMap(
                xl=self.xl,
                yl=self.yl,
                xh=self.xh,
                yh=self.yh,
                num_movable_nodes=self.num_movable_nodes,
                num_bins_x=self.num_bins_x,
                num_bins_y=self.num_bins_y,
                num_threads=self.num_threads
                )
        self.adjust_node_area_pin = AdjustNodeAreaFromPinMap(
                pin_weights=self.pin_weights, 
                flat_node2pin_start_map=self.flat_node2pin_start_map, 
                xl=self.xl,
                yl=self.yl,
                xh=self.xh,
                yh=self.yh,
                num_movable_nodes=self.num_movable_nodes,
                num_bins_x=self.num_bins_x,
                num_bins_y=self.num_bins_y,
                unit_pin_capacity=self.unit_pin_capacity, 
                num_threads=self.num_threads
                )

    def forward(self, 
            pos, 
            node_size_x, node_size_y, 
            pin_offset_x, pin_offset_y, 
            route_utilization_map, 
            pin_utilization_map
            ):
        # check the instance area adjustment is performed
        if (cur_metric_overflow > self.instance_area_adjust_overflow) or (not self.adjust_area_flag):
            return False

        self.max_total_area = (node_size_x[:num_movable_nodes] * node_size_y[:num_movable_nodes]
                               ).sum() + (node_size_x[-num_filler_nodes:] * node_size_y[-num_filler_nodes:]).sum()

        # compute routability optimized area
        if route_utilization_map is not None:
            route_opt_area = self.adjust_node_area_route(pos, route_utilization_map)

        # compute pin density optimized area
        if pin_utilization_map is not None:
            pin_opt_area = self.adjust_node_area_pin(pos, pin_utilization_map)

        # compute old areas of movable nodes
        node_size_x_movable = node_size_x[:self.num_movable_nodes]
        node_size_y_movable = node_size_y[:self.num_movable_nodes]
        old_movable_area = node_size_x_movable * node_size_y_movable
        old_movable_area_sum = old_movable_area.sum()

        # compute the extra area max(route_opt_area, pin_opt_area) over the base area for each movable node
        if route_utilization_map is not None and pin_utilization_map is not None: 
            area_increment = F.relu(torch.max(route_opt_area, pin_opt_area) - old_movable_area)
        elif route_utilization_map is not None: 
            area_increment = F.relu(route_opt_area - old_movable_area)
        else:
            area_increment = F.relu(pin_opt_area - old_movable_area)
        area_increment_sum = area_increment.sum()

        # check whether the total area is larger than the max area requirement
        # If yes, scale the extra area to meet the requirement
        # We assume the total base area is no greater than the max area requirement
        scale_factor = (self.max_total_area - old_movable_area_sum) / area_increment_sum

        # set the new_movable_area as base_area + scaled area increment
        if scale_factor <= 0:
            new_movable_area = old_movable_area
            area_increment_sum = 0
        elif scale_factor >= 1:
            new_movable_area = old_movable_area + area_increment
        else:
            new_movable_area = old_movable_area + area_increment * scale_factor
            area_increment_sum *= scale_factor
        new_movable_area_sum = old_movable_area_sum + area_increment_sum

        # compute the adjusted area increase ratio
        if route_utilization_map is not None: 
            route_area_increment_ratio = F.relu(route_opt_area - old_movable_area).sum() / old_movable_area_sum
        else:
            route_area_increment_ratio = None 
        if pin_utilization_map is not None: 
            pin_area_increment_ratio = F.relu(pin_opt_area - old_movable_area).sum() / old_movable_area_sum
        else:
            pin_area_increment_ratio = None
        area_increment_ratio = area_increment_sum / old_movable_area_sum

        # disable some of the area adjustment if the condition holds
        adjust_route_area_flag = route_area_increment_ratio is not None and route_area_increment_ratio.data.item() > self.route_area_adjust_stop_ratio
        adjust_pin_area_flag = pin_area_increment_ratio is not None and pin_area_increment_ratio.data.item() > self.pin_area_adjust_stop_ratio
        adjust_area_flag = (area_increment_ratio.data.item() > self.area_adjust_stop_ratio) and (adjust_route_area_flag self.adjust_pin_area_flag)

        if adjust_area_flag:
            num_nodes = pos.numel() / 2
            # adjust the size of movable nodes
            # each movable node have its own inflation ratio, the shape of movable_nodes_ratio is (num_movable_nodes)
            movable_nodes_ratio = torch.sqrt(new_movable_area / old_movable_area)
            pos[:self.num_movable_nodes] += node_size_x_movable * 0.5  
            pos[num_nodes:num_nodes + self.num_movable_nodes] += node_size_y_movable * 0.5  
            node_size_x_movable *= movable_nodes_ratio
            node_size_y_movable *= movable_nodes_ratio
            pos[:self.num_movable_nodes] -= node_size_x_movable * 0.5  
            pos[num_nodes:num_nodes + self.num_movable_nodes] -= node_size_y_movable * 0.5  

            # finally scale the filler instance areas to let the total area be max_total_area
            # all the filler nodes share the same deflation ratio, filler_nodes_ratio is a scalar
            node_size_x_filler = node_size_x[-self.num_filler_nodes:]
            node_size_y_filler = node_size_y[-self.num_filler_nodes:]
            old_filler_area_sum = (node_size_x_filler * node_size_y_filler).sum()
            new_filler_area_sum = F.relu(self.max_total_area - new_movable_area_sum)
            filler_nodes_ratio = torch.sqrt(new_filler_area_sum / old_filler_area_sum).data.item()
            pos[num_nodes - self.num_filler_nodes:num_nodes] += node_size_x_filler * 0.5 
            pos[-self.num_filler_nodes:] += node_size_y_filler * 0.5 
            node_size_x_filler *= filler_nodes_ratio
            node_size_y_filler *= filler_nodes_ratio
            pos[num_nodes - self.num_filler_nodes:num_nodes] -= node_size_x_filler * 0.5 
            pos[-self.num_filler_nodes:] -= node_size_y_filler * 0.5 

            if pos.is_cuda:
                update_pin_offset_cuda.forward(
                    self.flat_node2pin_start_map,
                    self.flat_node2pin_map,
                    self.num_movable_nodes,
                    self.num_filler_nodes,
                    movable_nodes_ratio,
                    filler_nodes_ratio,
                    pin_offset_x,
                    pin_offset_y
                )
            else:
                update_pin_offset_cpp.forward(
                    self.flat_node2pin_start_map,
                    self.flat_node2pin_map,
                    self.num_movable_nodes,
                    self.num_filler_nodes,
                    movable_nodes_ratio,
                    filler_nodes_ratio,
                    pin_offset_x,
                    pin_offset_y,
                    self.num_threads
                )
            return True 
        return False 